# Deploy setup (Stage 5)

Reference material for Will to read and apply himself. **Nothing in this
file has been run or applied by Claude Code** - per CLAUDE.md, nothing
touches the VPS without the exact command shown first and a yes.

BRIEF.md asks for a sample **Nginx** server block and a Caddy equivalent.
That's swapped here for **Apache**, since the real server is an Apache host
(`/var/www/html`, other domains on WordPress, confirmed with Will 6 Oct
2026) - Nginx/Caddy samples would be dead weight for a server that isn't
running either. Flagging the deviation explicitly rather than silently
answering a different question than the one BRIEF.md asked.

The site lives at `example.com/gene` - a **subdirectory of an existing
vhost's docroot**, not its own vhost (the same docroot will later host a
"weather watch" app at the root, per Will). Everything below is scoped to
that one subdirectory and its own `<Directory>` block, so it can't affect
anything else already served from `/var/www/html`.

## 1. Server-side: a dedicated SFTP-only, chrooted deploy account

BRIEF.md: "The deploy user is a dedicated SFTP-only, chrooted account.
Document the server-side setup for me as instructions; don't perform it."

Run these as root on the VPS (adjust the username/paths if you'd rather use
something other than `gwdeploy`):

```bash
# 1. Create the user with no shell (SFTP-only - it can never get a shell,
#    even if the chroot or SSH config were somehow misconfigured later).
sudo adduser --disabled-password --shell /usr/sbin/nologin gwdeploy

# 2. Generate a key pair FOR THIS ACCOUNT on your own machine (not the
#    server), then copy only the public key to the server:
#      ssh-keygen -t ed25519 -f gwdeploy_key -C "gedcomwright deploy"
#    This writes gwdeploy_key (private - goes in config.yaml's
#    publish.private_key_path, converted to .ppk for WinSCP via
#    PuTTYgen's "Load" + "Save private key") and gwdeploy_key.pub (public -
#    goes on the server, below).
sudo mkdir -p /home/gwdeploy/.ssh
sudo tee /home/gwdeploy/.ssh/authorized_keys < gwdeploy_key.pub
sudo chown -R root:root /home/gwdeploy        # chroot requires this
sudo chmod 755 /home/gwdeploy
sudo mkdir -p /home/gwdeploy/site
sudo chown gwdeploy:gwdeploy /home/gwdeploy/site
sudo chmod 700 /home/gwdeploy/.ssh
sudo chmod 600 /home/gwdeploy/.ssh/authorized_keys
sudo chown -R gwdeploy:gwdeploy /home/gwdeploy/.ssh
```

Why the chroot's top directory (`/home/gwdeploy`) is owned by **root**, not
`gwdeploy`: OpenSSH's `ChrootDirectory` refuses to activate unless every
directory in the chroot path up to and including it is owned by root and
not group/other-writable. The one directory the deploy account can
actually write into is the `site/` subdirectory under it.

Add to `/etc/ssh/sshd_config` (or a drop-in under `/etc/ssh/sshd_config.d/`):

```sshconfig
Match User gwdeploy
    ChrootDirectory /home/gwdeploy
    ForceCommand internal-sftp
    AllowTcpForwarding no
    X11Forwarding no
    PasswordAuthentication no
```

Then `sudo systemctl reload sshd` (not restart - reload re-reads config
without dropping existing sessions).

**Bind-mount the real docroot subdirectory into the chroot**, so the
deploy account's `site/` is actually `/var/www/html/gene`, not a separate
copy you'd have to sync again:

```bash
sudo mkdir -p /var/www/html/gene
sudo chown gwdeploy:gwdeploy /var/www/html/gene
# bind-mount so writes to the chroot land in the real docroot:
sudo mount --bind /var/www/html/gene /home/gwdeploy/site
# persist across reboots:
echo "/var/www/html/gene /home/gwdeploy/site none bind 0 0" | sudo tee -a /etc/fstab
```

`config.yaml`'s `publish.remote_path` is then `/site` (the path *as the
chrooted account sees it*, not `/var/www/html/gene` - SFTP inside a chroot
has no visibility outside its own root).

### Getting the host key fingerprint for `config.yaml`

From your own machine, with WinSCP installed, connect once interactively
(`Session > New Session`, same host/username/key) - WinSCP will show an
"unknown host key" prompt on first connection. Copy the fingerprint it
shows (there's a "Copy key fingerprint to clipboard" button) into
`config.yaml`'s `publish.host_key`. This is what lets every later
*scripted* connection verify the server non-interactively instead of
prompting.

### `config.yaml` additions

```yaml
publish:
  backend: winscp  # the default - can be omitted
  host: example.com
  username: gwdeploy
  remote_path: /site
  private_key_path: "C:\\Users\\you\\.ssh\\gwdeploy_key.ppk"
  host_key: "ssh-ed25519 255 <paste the fingerprint here>"
  # port: 22  # only needed if not the default
```

## 1a. Alternative backend: rsync over SSH (optional, broader access)

Will's choice (6 Oct 2026): offered as a simpler, more cross-platform
option than scripting a Windows GUI tool, for anyone who'd rather not
install WinSCP. **Read this whole section before using it** - it's opt-in
for a real reason, not just a style preference.

**The tradeoff, plainly:** rsync's protocol runs `rsync --server ...` as a
remote *command* over the SSH session - it needs a real shell, which the
SFTP-only chrooted account above (`ForceCommand internal-sftp`) can never
provide. `gedcomwright publish` prints a warning banner every time this
backend runs, as a standing reminder, not just at setup time.

### A new, dedicated account for this - not root, not an existing one

Will's choice (6 Oct 2026): a new account, scoped down as far as a
shell-requiring account reasonably can be, rather than reusing root or an
existing one. Run as root on the VPS:

```bash
# A real login shell (rsync needs it), but its own unprivileged account -
# not added to any group that owns other sites' files.
sudo adduser --disabled-password --gecos "" gwrsync

# Key-only, same pattern as section 1:
#   ssh-keygen -t ed25519 -f gwrsync_key -C "gedcomwright rsync deploy"
sudo mkdir -p /home/gwrsync/.ssh
sudo tee /home/gwrsync/.ssh/authorized_keys < gwrsync_key.pub
sudo chown -R gwrsync:gwrsync /home/gwrsync/.ssh
sudo chmod 700 /home/gwrsync/.ssh
sudo chmod 600 /home/gwrsync/.ssh/authorized_keys

# Scope it to /gene by OWNERSHIP, since there's no chroot this time:
# this account owns this one directory and nothing else on the server.
sudo chown -R gwrsync:gwrsync /var/www/html/gene
```

Add the same key-only hardening as section 1's `Match User` block, minus
the chroot/SFTP-only lines (this account needs its ordinary shell for
rsync to work at all):

```sshconfig
Match User gwrsync
    AllowTcpForwarding no
    X11Forwarding no
    PasswordAuthentication no
```

`sudo systemctl reload sshd` afterwards, same as section 1.

Apache doesn't need any extra configuration to keep serving these files:
ownership by `gwrsync` doesn't stop Apache (or anyone else) from *reading*
them, as long as they're left at the ordinary web-content permissions
(644 for files, 755 for directories) that `rsync -a` preserves from your
local `build/site/` by default - only *write* access is actually scoped by
this setup.

**What this ownership-based scoping does and doesn't contain** - read this
even though you've already decided on a new account, since it affects how
much of a boundary this actually is:
- **Contains:** `gwrsync` can only *write* within `/var/www/html/gene` -
  it doesn't own, and so can't modify or delete, anything else on the
  server, including the WordPress sites.
- **Does not contain:** *read* access elsewhere. A real shell account can
  still read any file on the server that's left world-readable, regardless
  of who owns it - Linux's ordinary permission model doesn't scope that by
  directory, only a chroot does (which rsync can't run inside). If any
  other site's files are world-readable - WordPress's `wp-config.php`
  (database credentials) is a common one worth checking - this account (or
  anyone who obtains its key) could read them. Worth a quick
  `ls -la wp-config.php` on the other sites as general server hygiene,
  independent of this project.
- **If you want a harder boundary later:** a properly jailed shell (e.g.
  via `jailkit`) is possible, but meaningfully more setup than either
  account above - it needs rsync's own binary and its shared libraries
  copied into the jail, not just a directory restriction. Not written up
  here unless you want it; the `winscp` backend's SFTP-only chroot
  (section 1) already gets you that level of containment for free, which
  is exactly why it's the default.

**What this needs, beyond the account itself:**
- `rsync` installed **on the VPS** (near-universal on Linux - check with
  `rsync --version`; almost certainly already there).
- `rsync` installed **on this Windows machine** - confirmed NOT present
  here as of 6 Oct 2026 (not bundled with Windows, not bundled with plain
  Git for Windows either). Options: Git for Windows' installer has an
  optional "Unix tools" component that can include it; WSL has it by
  default; or a standalone Windows rsync build. Install one before trying
  this backend for real.
- The server's host key accepted once, interactively, into this machine's
  own SSH known_hosts (rsync/ssh's ordinary mechanism - not WinSCP's
  `-hostkey=` fingerprint, which doesn't apply here):
  ```powershell
  ssh -i C:\Users\you\.ssh\gwrsync_key gwrsync@example.com exit
  ```
  Answer "yes" to the "authenticity of host" prompt once; every later
  scripted run then verifies against that pinned key automatically
  (`StrictHostKeyChecking=yes`, set by `publish.py` itself).

### `config.yaml` for this backend

```yaml
publish:
  backend: rsync
  host: example.com
  username: gwrsync
  remote_path: /var/www/html/gene
  private_key_path: "C:\\Users\\you\\.ssh\\gwrsync_key"
  # no host_key field - rsync/ssh uses known_hosts instead, see above
```

## 2. Apache: serving `/gene`

Add this `<Directory>` block to the **existing vhost** that already serves
`/var/www/html` (its `.conf` file under `sites-available/`, or
`/etc/apache2/apache2.conf` if there's no separate vhost file) - not a new
vhost:

```apache
<Directory /var/www/html/gene>
    Options -Indexes +FollowSymLinks
    AllowOverride None
    Require all granted

    # The 404 page (build/site/404.html, unnested) - make Apache actually
    # serve it on a real 404 instead of its own default error page.
    ErrorDocument 404 /gene/404.html

    # Long cache for hashed/versioned assets: static/ (fonts/css/js/vendor)
    # and media/ derivatives are all content-hash-named, so a changed file
    # is a new filename - safe to cache for a year.
    <FilesMatch "\.(woff2|css|js)$">
        Header set Cache-Control "public, max-age=31536000, immutable"
    </FilesMatch>
    <LocationMatch "^/gene/media/">
        Header set Cache-Control "public, max-age=31536000, immutable"
    </LocationMatch>

    # Short cache for HTML - content changes on every rebuild, same URL.
    <FilesMatch "\.html$">
        Header set Cache-Control "public, max-age=600"
    </FilesMatch>
</Directory>

# Compression (mod_deflate is near-universal; mod_brotli needs a newer
# Apache build - include both, Apache picks whichever the client accepts).
<IfModule mod_deflate.c>
    AddOutputFilterByType DEFLATE text/html text/css application/javascript application/json image/svg+xml
</IfModule>
<IfModule mod_brotli.c>
    AddOutputFilterByType BROTLI_COMPRESS text/html text/css application/javascript application/json image/svg+xml
</IfModule>
```

Requires `mod_headers` and `mod_expires` enabled (`sudo a2enmod headers
expires deflate`, then reload Apache) for the `Header set` directives to
work. `.webp` images aren't in the compression list - they're already
compressed; gzipping them again wastes CPU for no size benefit.

### HTTPS

Assuming the vhost already has Let's Encrypt via certbot for `example.com`
(likely, given WordPress sites are presumably already on HTTPS on the same
box) - `/gene` inherits that certificate automatically, since it's a path
under the same vhost/domain, not a separate one. Nothing extra needed here
unless `example.com` itself isn't on HTTPS yet, in which case that's a
prerequisite independent of this project:

```bash
sudo certbot --apache -d example.com
```

### A note on the bind mount and Apache

Apache serves `/var/www/html/gene` directly; the chroot bind-mount (above)
makes the SFTP account's view of `/site` be the *same* files, so nothing
extra is needed on the Apache side to "see" what gets uploaded - it's
already the live docroot.

## 3. First deploy

BRIEF.md: "Expect a slow first upload. Atomic deploys are out of scope."
The first `gedcomwright publish --live` has nothing to diff against
remotely, so it uploads the full ~2GB media tree, not just changes -
expect this to take a while depending on the VPS's upload bandwidth.
Every deploy after that only pushes changed files - WinSCP's
`-criteria=either` (size or time) or rsync's own default comparison
(size and time), whichever backend is configured.

Not atomic: a deploy in progress is visible mid-upload (a partially
updated site), and neither backend does the "upload to a temp path then
swap" trick some other deploy tools use. Acceptable for this project's
traffic profile per the brief ("out of scope"), but worth knowing before
pushing a large change at a busy time.
