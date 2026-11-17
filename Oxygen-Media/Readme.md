# Oxygen Chat

A tiny self-hosted chat app. One Python file for the server, three plain files for the frontend, no dependencies.

I made it because I wanted a chat I could actually read and change in one sitting. No framework, no build step, no CDN, no proprietary JavaScript. If you can read basic Python and HTML, you can hack on all of it.

The default look is intentionally plain: default browser fonts, simple CSS, black background, white text, red borders, dark blue lines. It's meant to be a starting point that you restyle into your own thing.

## What it does

- **General room** that every signed-in user can see.
- **Private rooms** for 2 to 20 people. Creating one gives you a random code, and the only way in is to type that code.
- **File sharing**: zip, html, php, whatever. Up to 20 MB each.
- **Accounts without passwords you choose.** You sign up with just a username and the server gives you a random key. That key is your password from then on.
- **HTTPS only.** Plain HTTP on port 8080 redirects to HTTPS.
- **Lockout**: 10 wrong keys or room codes in a row and you're blocked for 20 minutes.

## Quick start

You need Python 3.8 or newer (I built it on 3.12) and the `openssl` command, which is only used once to make a certificate.

```
python3 server.py
```

Then open https://localhost:8443.

Your browser will warn you about the certificate. That's expected, because the certificate is self-signed. Click *Advanced* and then *Proceed*. If you'd rather not see the warning at all, see [Certificates](#certificates) below.

On first run it creates `chat.db`, `uploads/` and `certs/` next to `server.py`.

## How accounts work

There's no email and no password field. When you sign up, the server shows you a key once, something like `vtHUjrqxpaGExCyup-JX9w`. Save it. To sign in later you type your username and that key.

The key is stored only as a hash, so **if you lose it, nobody can recover it** (including you). That's a deliberate trade-off to keep things simple.

## Certificates

Browsers check the certificate against the exact name or IP you typed in the address bar, so a certificate for `localhost` doesn't work when you open the site by your LAN IP. The server handles this for you: on startup it makes a self-signed certificate covering `localhost`, `127.0.0.1`, your machine name and your LAN IP, and it re-makes it when any of those change.

Need another name or IP in there?

```
python3 server.py chat.example.com 192.168.1.50
```

Other options, from simplest to most "real":

1. **Your own certificate.** Put `cert.pem` and `key.pem` in `certs/` and the server will use those instead.
2. **[mkcert](https://github.com/FiloSottile/mkcert)** for local use without warnings:
   ```
   mkcert -install
   mkcert -cert-file certs/cert.pem -key-file certs/key.pem localhost 127.0.0.1 192.168.1.50
   ```
   Other devices (your phone, for example) need to trust mkcert's root CA too, or you click through the warning there.
3. **Behind a reverse proxy.** If you're putting this on the internet, let something like [Caddy](https://caddyserver.com/) handle HTTPS and run:
   ```
   python3 server.py --http
   caddy reverse-proxy --from chat.example.com --to 127.0.0.1:8080
   ```
   In this mode the server listens on `127.0.0.1:8080` only, still refuses to serve anything that didn't arrive over HTTPS, and reads the visitor's real IP from `X-Forwarded-For` so the lockout works per person and not per proxy.

## Project layout

```
server.py        the whole backend (standard library only)
web/
  index.html     the page
  style.css      the look
  app.js         the logic (vanilla JS)
certs/           TLS certificate and key (created on first run)
uploads/         uploaded files (created on first run)
chat.db          SQLite database (created on first run)
```

The server reads the frontend files on every request, so you can edit `web/` and just refresh the page. No restart needed.

If you put the three frontend files right next to `server.py` without a `web/` folder, that works too. It only ever serves those three exact filenames, so nothing else in the folder is reachable.

## Making it yours

- **Colors and layout:** `web/style.css`. It's about 20 lines.
- **Text and structure:** `web/index.html`.
- **Limits and ports:** the constants at the top of `server.py` (`HTTPS_PORT`, `MAX_ROOM`, `MAX_UPLOAD`, `MAX_FAILS`, `LOCK_SECONDS`).

Messages show up through simple polling every two seconds. That keeps the code small. If you want instant delivery, WebSockets are the obvious next step, though it means leaving the standard library behind or writing a WebSocket handshake by hand.

## Security notes

I tried to do the basics properly, but nobody has audited this, so please read this section before you trust it with anything important.

What's in place:

- Messages are rendered with `textContent`, never `innerHTML`, and the server sends a strict Content-Security-Policy (`default-src 'self'`), so injected HTML and scripts don't run.
- Keys and session tokens are random and stored hashed. Cookies are `Secure`, `HttpOnly` and `SameSite=Strict`.
- Uploaded files get a random name on disk and are only ever sent back as a download with `nosniff`. The server never executes or renders them.
- Files and private rooms can only be reached by people who are inside that room.

What to keep in mind:

- **It's not end-to-end encrypted.** Messages and files sit unencrypted in `chat.db` and `uploads/`, and whoever runs the server can read them.
- **The lockout works per username too**, so someone who knows your username can lock you out for 20 minutes by guessing wrong on purpose. That's the cost of the rule as specified.
- A downloaded `.html` or `.php` file is as dangerous as any file from a stranger. Don't open things you don't trust.
- Sign-ups aren't rate limited, and there's no message deletion or moderation yet.
- It uses Python's built-in `http.server`, which is fine for friends and small groups. For anything bigger, put it behind a proper reverse proxy.

Found a security problem? Please report it privately by email (address below) before opening a public issue.

## Support the project

If it's useful to you and you'd like to support it, you can donate by email: **oxyd1um@proton.me**

Bug reports, ideas and pull requests are welcome too. The one rule is to keep it small and readable. A feature that needs a new dependency is probably better as a fork.

## License

See [LICENSE](LICENSE).
