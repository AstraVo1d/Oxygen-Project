#!/usr/bin/env python3
import os, re, sys, json, ssl, time, hmac, socket, secrets, sqlite3, hashlib, tempfile, threading, traceback, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs, quote

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
CERTS = os.path.join(BASE, "certs")
UPLOADS = os.path.join(BASE, "uploads")
DB = os.path.join(BASE, "chat.db")
HTTPS_PORT, HTTP_PORT = 8443, 8080
MAX_ROOM = 20                          
MAX_UPLOAD = 20 * 1024 * 1024          # 20 MB
MAX_FAILS, LOCK_SECONDS = 10, 20 * 60  
PROXY = "--http" in sys.argv
ARGS = [a for a in sys.argv[1:] if a != "--http"]

PAGES = {                            
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

os.makedirs(UPLOADS, exist_ok=True)


def q(sql, args=()):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        with c:
            return c.execute(sql, args).fetchall()
    finally:
        c.close()

q("CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY, key_hash TEXT)")
q("CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user TEXT)")
q("CREATE TABLE IF NOT EXISTS rooms(code TEXT PRIMARY KEY)")
q("CREATE TABLE IF NOT EXISTS members(room TEXT, user TEXT, PRIMARY KEY(room, user))")
q("CREATE TABLE IF NOT EXISTS msgs(id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT, "
  "user TEXT, text TEXT, file TEXT, fname TEXT, ts INTEGER)")


def h(s): return hashlib.sha256(s.encode()).hexdigest()   

fails = {}   # "ip:x" / "user:y" -> (count, last_fail, locked_until)
def locked(*keys):
    now = time.time()
    return any(fails.get(k, (0, 0, 0))[2] > now for k in keys)

def fail(*keys):
    now = time.time()
    for k in keys:
        n, last, _ = fails.get(k, (0, 0, 0))
        if now - last > LOCK_SECONDS: n = 0
        n += 1
        fails[k] = (n, now, now + LOCK_SECONDS if n >= MAX_FAILS else 0)

def webfile(name):                                  # ./web/name, or flat next to server.py
    for d in (WEB, BASE):
        p = os.path.join(d, name)
        if os.path.isfile(p): return p


class Chat(BaseHTTPRequestHandler):
    timeout = 30                                   
    def log_message(self, *a): pass

    
    def raw(self, code, data, ctype, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Cache-Control", "no-store")
        for k, v in headers: self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def js(self, obj, code=200, headers=()): self.raw(code, json.dumps(obj).encode(), "application/json", headers)
    def err(self, msg, code=400): self.js({"error": msg}, code)

    
    def ip(self):
        if PROXY:                                   
            return self.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or self.client_address[0]
        return self.client_address[0]

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 10000: return {}
        try: d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError: return {}
        return d if isinstance(d, dict) else {}

    def user(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" not in c: return None
        r = q("SELECT user FROM sessions WHERE token=?", (h(c["sid"].value),))
        return r[0]["user"] if r else None

    def need(self):
        u = self.user()
        if not u: self.err("Sign in first", 401)
        return u

    def can(self, room):
        u = self.user()
        if not u: return False
        if room == "general": return True
        return bool(q("SELECT 1 FROM members WHERE room=? AND user=?", (room, u)))

    def start(self, name):                          
        tok = secrets.token_urlsafe(32)
        q("INSERT INTO sessions VALUES(?,?)", (h(tok), name))
        return [("Set-Cookie", "sid=%s; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=2592000" % tok)]

    
    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")

    def route(self, method):
        u = urlparse(self.path)
        self.qs, p = parse_qs(u.query), u.path
        if PROXY and self.headers.get("X-Forwarded-Proto") != "https":     # force HTTPS behind a proxy
            loc = "https://%s%s" % (self.headers.get("Host", "localhost"), self.path)
            return self.raw(301, b"", "text/plain", [("Location", loc)])
        api = {
            ("POST", "/api/signup"): self.signup, ("POST", "/api/login"): self.login,
            ("POST", "/api/logout"): self.logout, ("GET", "/api/me"): self.whoami,
            ("GET", "/api/rooms"): self.rooms, ("POST", "/api/create"): self.create,
            ("POST", "/api/join"): self.join, ("GET", "/api/msgs"): self.msgs,
            ("POST", "/api/send"): self.say, ("POST", "/api/upload"): self.upload,
        }
        try:
            if method == "GET" and p in PAGES: self.page(p)
            elif method == "GET" and p.startswith("/f/"): self.download(p[3:])
            elif (method, p) in api: api[(method, p)]()
            else: self.err("Not found", 404)
        except ValueError: self.err("Bad request")
        except OSError: pass                        # client went away / TLS hiccup: nothing to answer
        except Exception:
            traceback.print_exc()                   # real bugs are shown HERE in the console
            try: self.err("Server error", 500)
            except OSError: pass

    def page(self, p):
        name, ctype = PAGES[p]
        path = webfile(name)
        if not path:
            print("Missing file: web/%s (put index.html, style.css, app.js in ./web)" % name)
            return self.err("Missing web/%s" % name, 404)
        with open(path, "rb") as f:
            self.raw(200, f.read(), ctype)

    # --- accounts
    def signup(self):
        name = str(self.body().get("name", "")).strip()
        if not re.fullmatch(r"\w{3,20}", name):
            return self.err("Username: 3-20 letters, numbers or _")
        key = secrets.token_urlsafe(16)             # your random password
        try: q("INSERT INTO users VALUES(?,?)", (name, h(key)))
        except sqlite3.IntegrityError: return self.err("Username taken")
        self.js({"name": name, "key": key}, headers=self.start(name))

    def login(self):
        d = self.body()
        name, key = str(d.get("name", "")).strip(), str(d.get("key", ""))
        ks = ("ip:" + self.ip(), "user:" + name)
        if locked(*ks):
            return self.err("Too many wrong tries. Blocked for 20 minutes.", 429)
        row = q("SELECT key_hash FROM users WHERE name=?", (name,))
        if not row or not hmac.compare_digest(row[0]["key_hash"], h(key)):
            fail(*ks)
            return self.err("Wrong username or key")
        fails.pop("user:" + name, None)
        self.js({"name": name}, headers=self.start(name))

    def logout(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" in c: q("DELETE FROM sessions WHERE token=?", (h(c["sid"].value),))
        self.js({"ok": 1}, headers=[("Set-Cookie", "sid=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0")])

    def whoami(self):
        u = self.need()
        if u: self.js({"name": u})

    # --- rooms
    def rooms(self):
        u = self.need()
        if not u: return
        mine = [r["room"] for r in q("SELECT room FROM members WHERE user=?", (u,))]
        self.js(["general"] + mine)

    def create(self):
        u = self.need()
        if not u: return
        code = secrets.token_urlsafe(9)             # the code IS the key to the room
        q("INSERT INTO rooms VALUES(?)", (code,))
        q("INSERT INTO members VALUES(?,?)", (code, u))
        self.js({"room": code})

    def join(self):
        u = self.need()
        if not u: return
        ip = "ip:" + self.ip()
        if locked(ip):
            return self.err("Too many wrong tries. Blocked for 20 minutes.", 429)
        code = str(self.body().get("code", "")).strip()
        if not q("SELECT 1 FROM rooms WHERE code=?", (code,)):
            fail(ip)
            return self.err("Wrong code")
        if not self.can(code) and q("SELECT COUNT(*) c FROM members WHERE room=?", (code,))[0]["c"] >= MAX_ROOM:
            return self.err("Room is full (20 people max)")
        q("INSERT OR IGNORE INTO members VALUES(?,?)", (code, u))
        self.js({"room": code})

    # --- messages & files
    def msgs(self):
        room = self.qs.get("room", [""])[0]
        after = int(self.qs.get("after", ["0"])[0])
        if not self.need(): return
        if not self.can(room): return self.err("No access", 403)
        rows = q("SELECT * FROM (SELECT id,user,text,file,fname,ts FROM msgs WHERE room=? AND id>? "
                 "ORDER BY id DESC LIMIT 100) ORDER BY id", (room, after))
        self.js([dict(r) for r in rows])

    def say(self):
        u = self.need()
        if not u: return
        d = self.body()
        room, text = str(d.get("room", "")), str(d.get("text", "")).strip()[:2000]
        if not self.can(room): return self.err("No access", 403)
        if not text: return self.err("Empty message")
        q("INSERT INTO msgs(room,user,text,ts) VALUES(?,?,?,?)", (room, u, text, int(time.time())))
        self.js({"ok": 1})

    def upload(self):                               # raw file body, any type (zip, html, php...)
        u = self.need()
        if not u: return
        room = self.qs.get("room", [""])[0]
        name = os.path.basename(self.qs.get("name", ["file"])[0])
        name = re.sub(r"[^\w.\-]", "_", name).lstrip(".")[:100] or "file"
        if not self.can(room): return self.err("No access", 403)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0: return self.err("Empty file")
        if n > MAX_UPLOAD: return self.err("File too big (20 MB max)", 413)
        fid, left = secrets.token_hex(16), n        # random name on disk, never executed
        path = os.path.join(UPLOADS, fid)
        with open(path, "wb") as f:
            while left > 0:
                chunk = self.rfile.read(min(65536, left))
                if not chunk: break
                f.write(chunk)
                left -= len(chunk)
        if left:
            os.remove(path)
            return self.err("Upload interrupted")
        q("INSERT INTO msgs(room,user,text,file,fname,ts) VALUES(?,?,?,?,?,?)",
          (room, u, "", fid, name, int(time.time())))
        self.js({"ok": 1})

    def download(self, fid):                        # always a download, never opened in the browser
        r = q("SELECT room,fname FROM msgs WHERE file=?", (fid,))
        path = os.path.join(UPLOADS, fid)
        if not r or not self.can(r[0]["room"]) or not os.path.isfile(path):
            return self.err("Not found", 404)
        with open(path, "rb") as f:
            self.raw(200, f.read(), "application/octet-stream",
                     [("Content-Disposition", "attachment; filename*=UTF-8''" + quote(r[0]["fname"]))])

class Quiet(ThreadingHTTPServer):
    def handle_error(self, request, client_address):   # browser rejecting a self-signed cert is normal
        if not isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)

class Redirect(BaseHTTPRequestHandler):             # plain HTTP -> HTTPS
    def do_GET(self):
        host = self.headers.get("Host", "localhost").split(":")[0]
        self.send_response(301)
        self.send_header("Location", "https://%s:%d%s" % (host, HTTPS_PORT, self.path))
        self.send_header("Content-Length", "0")
        self.end_headers()
    do_POST = do_GET
    def log_message(self, *a): pass

# ---------- certificacets (IGNORED BTW)
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))            
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"

def make_cert(cert, key, san):
    
    conf = ("[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=ext\n[dn]\nCN=localhost\n"
            "[ext]\nsubjectAltName=%s\nextendedKeyUsage=serverAuth\n" % san)
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as f:
        f.write(conf)
    try:
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
                            "-keyout", key, "-out", cert, "-config", f.name], capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("`openssl` not found. Install it (Windows: comes with Git for Windows), or put your own\n"
                 "certs/cert.pem + certs/key.pem (mkcert / Let's Encrypt), or run: python3 server.py --http")
    finally:
        os.remove(f.name)
    if r.returncode:
        sys.exit("openssl failed:\n" + r.stderr)
    os.chmod(key, 0o600)

def tls_files():
    os.makedirs(CERTS, exist_ok=True)
    own = (os.path.join(CERTS, "cert.pem"), os.path.join(CERTS, "key.pem"))
    if all(os.path.exists(p) for p in own):
        return own                                  
    cert, key, sanf = (os.path.join(CERTS, n) for n in ("auto-cert.pem", "auto-key.pem", "auto.san"))
    names = {"localhost", "127.0.0.1", socket.gethostname(), lan_ip(), *ARGS}
    san = ",".join(("IP:" if re.fullmatch(r"[\d.]+", n) else "DNS:") + n for n in sorted(names))
    fresh = (os.path.exists(cert) and os.path.exists(key) and os.path.exists(sanf)
             and open(sanf).read() == san and time.time() - os.path.getmtime(cert) < 300 * 86400)
    if not fresh:
        make_cert(cert, key, san)
        open(sanf, "w").write(san)
        print("Made self-signed cert for:", san)
    return cert, key


if __name__ == "__main__":
    if PROXY:
        srv = Quiet(("127.0.0.1", HTTP_PORT), Chat)
        print("Behind-proxy mode: http://127.0.0.1:%d  (proxy must send X-Forwarded-Proto and X-Forwarded-For)" % HTTP_PORT)
    else:
        cert, key = tls_files()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            ctx.load_cert_chain(cert, key)
        except (ssl.SSLError, OSError) as e:
            sys.exit("Bad certificate files in ./certs (%s).\nDelete them and restart." % e)
        srv = Quiet(("0.0.0.0", HTTPS_PORT), Chat)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
        threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Redirect).serve_forever, daemon=True).start()
        print("Running on https://localhost:%d" % HTTPS_PORT)
    srv.serve_forever()
