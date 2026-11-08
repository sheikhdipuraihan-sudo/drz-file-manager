import os, re, io, sys, tempfile, json, shutil
tmp = tempfile.mkdtemp(prefix='drz-test-')
os.environ['DRZ_DATA_DIR'] = tmp
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['DRZ_STORAGE'] = 'local'
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'api'))
import index

app = index.app
app.config['TESTING'] = True
c = app.test_client()

def csrf(html):
    m = re.search(r'name="_csrf_token" value="([^"]+)"', html)
    assert m, 'no csrf token in page'
    return m.group(1)

def check(name, cond, extra=''):
    print(('PASS' if cond else 'FAIL'), name, extra)
    if not cond:
        raise SystemExit(1)

# public
r = c.get('/')
check('landing', r.status_code == 200 and b'beautifully organized' in r.data)
r = c.get('/login')
check('login page', r.status_code == 200)
tok = csrf(r.data.decode())

# login
r = c.post('/login', data={'_csrf_token': tok, 'username': 'admin', 'password': '00000000'}, follow_redirects=False)
check('login redirect', r.status_code in (302, 303), str(r.status_code))

# files
r = c.get('/')
check('files page', r.status_code == 200 and b'Upload files' in r.data)
html = r.data.decode(); tok = csrf(html)

# create folder
r = c.post('/folder/create', data={'_csrf_token': tok, 'name': 'Docs', 'parent_id': ''}, follow_redirects=True)
check('create folder', r.status_code == 200 and b'Folder created' in r.data)

# find folder id
import sqlite3
db = sqlite3.connect(os.path.join(tmp, 'filemanager.db')); db.row_factory = sqlite3.Row
folder = db.execute("select * from folders where name='Docs'").fetchone()
check('folder exists', folder is not None)

# upload file
r = c.post('/upload', data={'_csrf_token': tok, 'folder_id': str(folder['id']),
                            'files': (io.BytesIO(b'hello world\nsecond line'), 'hello.txt')},
           content_type='multipart/form-data', follow_redirects=True)
check('upload', r.status_code == 200 and b'Uploaded 1 file' in r.data)
f = db.execute("select * from files where original_name='hello.txt'").fetchone()
check('file row', f is not None)

# preview + download
r = c.get('/preview/%d' % f['id'])
check('preview', r.status_code == 200 and b'hello world' in r.data)
r = c.get('/download/%d' % f['id'])
check('download', r.status_code == 200)

# search api
r = c.get('/api/search?q=hello')
check('api search', r.status_code == 200 and b'hello.txt' in r.data)
r = c.get('/api/file/%d' % f['id'])
check('api file', r.status_code == 200 and b'hello.txt' in r.data)

# star
r = c.post('/file/%d/star' % f['id'], data={'_csrf_token': tok}, headers={'X-CSRF-Token': tok})
check('star', r.status_code == 200 and r.get_json()['starred'] is True)

# rename
r = c.post('/file/%d/rename' % f['id'], data={'_csrf_token': tok, 'name': 'renamed.txt'}, follow_redirects=True)
check('rename', r.status_code == 200 and b'File renamed' in r.data)

# share
r = c.post('/share/%d' % f['id'], data={'_csrf_token': tok, 'hours': '24', 'max_downloads': '5',
                                        'password': '', 'note': 'test', 'allow_preview': '1'},
           follow_redirects=True)
check('share create', r.status_code == 200 and b'Share link created' in r.data)
s = db.execute("select * from share_links where file_id=?", (f['id'],)).fetchone()
check('share row', s is not None)
r = c.get('/s/%s' % s['token'])
check('public share', r.status_code == 200 and b'renamed.txt' in r.data)
r = c.get('/s/%s/download' % s['token'])
check('public download', r.status_code == 200)

# account
r = c.get('/account')
check('account', r.status_code == 200 and b'Change password' in r.data)
html = r.data.decode(); tok = csrf(html)
r = c.post('/account', data={'_csrf_token': tok, 'form_type': 'profile', 'display_name': 'Admin User',
                             'theme': 'dark', 'accent': 'emerald'}, follow_redirects=True)
check('profile update', r.status_code == 200 and b'Profile updated' in r.data)
r = c.get('/account/export')
check('export', r.status_code == 200 and b'renamed.txt' in r.data)

# admin pin setup
r = c.get('/admin')
check('admin redirect to pin', r.status_code in (302, 303))
r = c.get('/admin/pin')
check('pin page', r.status_code == 200)
html = r.data.decode(); tok = csrf(html)
r = c.post('/admin/pin', data={'_csrf_token': tok, 'pin': '123456', 'pin2': '123456'}, follow_redirects=True)
check('pin setup', r.status_code == 200 and b'Admin dashboard' in r.data)

# admin pages
for path, needle in [('/admin', b'All users'), ('/admin/logs', b'Audit log'), ('/admin/monitor', b'Eye in the sky'),
                     ('/admin/settings', b'Application settings'), ('/admin/backup', b'exported_at')]:
    r = c.get(path)
    check('admin ' + path, r.status_code == 200 and needle in r.data)

# admin settings post
html = c.get('/admin/settings').data.decode(); tok = csrf(html)
r = c.post('/admin/settings', data={'_csrf_token': tok, 'app_name': 'DRZ Test', 'max_upload_mb': '1024',
                                    'default_quota_mb': '0', 'trash_retention_days': '7',
                                    'share_default_hours': '24', 'footer_note': 'Test', 'allow_registration': '1'},
           follow_redirects=True)
check('settings save', r.status_code == 200 and b'Settings saved' in r.data)

# register
c2 = app.test_client()
r = c2.get('/register')
check('register page', r.status_code == 200)
html = r.data.decode(); tok = csrf(html)
r = c2.post('/register', data={'_csrf_token': tok, 'username': 'newuser', 'password': 'password123',
                               'password2': 'password123'}, follow_redirects=True)
check('register', r.status_code == 200 and b'Welcome' in r.data)

# bulk zip
html = c.get('/').data.decode(); tok = csrf(html)
r = c.post('/bulk', data={'_csrf_token': tok, 'action': 'download', 'ids': str(f['id'])})
check('bulk zip', r.status_code == 200 and r.mimetype == 'application/zip')

# trash delete/restore
html = c.get('/').data.decode(); tok = csrf(html)
r = c.post('/delete/%d' % f['id'], data={'_csrf_token': tok}, follow_redirects=True)
check('delete to trash', r.status_code == 200 and b'Moved to trash' in r.data)
t = db.execute("select * from trash where original_name='renamed.txt'").fetchone()
check('trash row', t is not None)
html = c.get('/trash').data.decode(); tok = csrf(html)
r = c.post('/trash/restore/%d' % t['id'], data={'_csrf_token': tok}, follow_redirects=True)
check('restore', r.status_code == 200 and b'Restored' in r.data)

# remote (catbox-backed) file behaviour
db.execute("INSERT INTO files (user_id, folder_id, filename, original_name, file_size, mime, upload_date, updated_at, remote_url) "
           "VALUES (1, NULL, 'remote_test.txt', 'remote.txt', 5, 'text/plain', ?, ?, 'https://files.catbox.moe/abc123.txt')",
           (index.now_str(), index.now_str()))
db.commit()
rf = db.execute("SELECT * FROM files WHERE original_name='remote.txt'").fetchone()
check('remote row', rf is not None and rf['remote_url'].startswith('https://files.catbox.moe/'))
r = c.get('/download/%d' % rf['id'])
check('remote download redirect', r.status_code == 302 and b'files.catbox.moe' in r.headers['Location'].encode())
r = c.get('/preview/%d' % rf['id'])
check('remote preview redirect', r.status_code == 302 and b'files.catbox.moe' in r.headers['Location'].encode())
html = c.get('/').data.decode(); tok = csrf(html)
r = c.post('/share/%d' % rf['id'], data={'_csrf_token': tok, 'hours': '24', 'max_downloads': '0',
                                        'password': '', 'note': '', 'allow_preview': '1'}, follow_redirects=True)
rs = db.execute("SELECT * FROM share_links WHERE file_id=?", (rf['id'],)).fetchone()
check('remote share create', r.status_code == 200 and rs is not None)
r = c.get('/s/%s/download' % rs['token'])
check('remote share redirect', r.status_code == 302 and b'files.catbox.moe' in r.headers['Location'].encode())
r = c.post('/delete/%d' % rf['id'], data={'_csrf_token': tok}, follow_redirects=True)
rt = db.execute("SELECT * FROM trash WHERE original_name='remote.txt'").fetchone()
check('remote trash keeps url', rt is not None and rt['remote_url'].startswith('https://files.catbox.moe/'))
html = c.get('/trash').data.decode(); tok = csrf(html)
r = c.post('/trash/restore/%d' % rt['id'], data={'_csrf_token': tok}, follow_redirects=True)
rr = db.execute("SELECT * FROM files WHERE original_name='remote.txt'").fetchone()
check('remote restore keeps url', r.status_code == 200 and rr is not None and rr['remote_url'].startswith('https://files.catbox.moe/'))

# assets
for path, needle in [('/favicon.svg', b'<svg'), ('/manifest.webmanifest', b'DRZ'), ('/robots.txt', b'Disallow'),
                     ('/health', b'status'), ('/sw.js', b'addEventListener')]:
    r = c.get(path)
    check('asset ' + path, r.status_code == 200 and needle in r.data)

# 404
r = c.get('/nope')
check('404 page', r.status_code == 404 and b'Not found' in r.data)

# regression: View/Share onclick handlers must stay valid HTML (JSON args need escaped quotes)
r = c.get('/')
page = r.data.decode()
btns = re.findall(r'onclick="((?:openPreview|openShare)\([^"]*)"', page)
check('view/share buttons rendered', len(btns) > 0, str(len(btns)))
check('view/share onclick not broken by raw quotes', all('&#34;' in b for b in btns))

print('ALL TESTS PASSED')
shutil.rmtree(tmp, ignore_errors=True)
