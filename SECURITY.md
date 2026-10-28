# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.6.x (latest) | ✅ security fixes |
| < 0.6.0 | ❌ never publicly released; upgrade to 0.6.x |

Fixes are released as a new package release (e.g. `0.6.0-2`) and noted in
the package `CHANGELOG.md` and a GitHub security advisory.

## Reporting a vulnerability

**Please don't open a public issue for security problems.**

Report privately through GitHub: **Security → Report a vulnerability** on this
repository ([direct link](https://github.com/raymondlmy/openwrt-traffic-quota/security/advisories/new)).

Please include:

- the package and version (`apk list -I | grep -E 'r3s|luci-app-r3s'`) and the
  OpenWrt release;
- what an attacker needs: LAN access, a LuCI login (read-only or full), shell
  access, or control of DNS answers;
- steps to reproduce, and what happens versus what should happen.

What to expect:

| | |
|---|---|
| Acknowledgement | within 7 days |
| First assessment (valid? severity?) | within 14 days |
| Fix for confirmed high/critical issues | aimed for within 30 days |
| Credit | in the advisory, unless you'd rather not be named |

This is a small volunteer project, so these are goals, not guarantees. You'll
be kept informed if something takes longer.

## Scope

**In scope:** everything in this repository:

- the `r3s-accounting`, `luci-app-r3s` and `r3s-accounting-telegram` packages;
- their scripts, init files, nft rules and the LuCI pages.

Examples of issues we want to hear about:

- **Bypassing enforcement.** A device escapes a schedule, block or quota it
  should be subject to, beyond the limitations documented in the README.
- **Privilege problems in LuCI.** A read-only LuCI user can change something,
  or any LuCI user can run commands or write files beyond what the page needs.
- **Injection.** Device names, domains, MACs, interface names, DNS answers or
  Telegram values reaching nft, the shell or SQL unsafely.
- **Leaks.** The Telegram token or report contents exposed to other users or
  hosts.
- **Damage to the router's own security.** An install or upgrade that weakens
  the firewall or overwrites user data.

**Out of scope:**

- **Limitations the README already documents:**
  - flow offloading;
  - clients using their own DNS-over-HTTPS;
  - devices with new or randomised MACs before they are approved;
  - the up-to-60-second enforcement delay after a firewall reload.
- **Attacks that need root on the router**, which already controls everything.
- **Problems in OpenWrt, AdGuard Home, Python or Telegram themselves.** Report
  those upstream.

## Security design

- **Nothing listens on the network.** The only interfaces are LuCI (OpenWrt's
  own authentication) and the command line.
- **Least privilege in LuCI.** Read-only users can only run
  `r3s-policy-ro` (viewing subcommands, no options) and `r3s-telegram-status`.
  Changes need write access.
- **Validated before nft.** Addresses are parsed with `ipaddress`, MACs must be
  canonical, domains must match a hostname pattern, and interface names must be
  plain Linux names. Values are passed to nft scripts, never through a shell.
- **The Telegram token** is stored mode 600, never displayed, and never passed
  on a command line (curl reads the request URL from stdin). The token and chat
  ID are strictly validated, because the report scripts source that file.
- **User data is never packaged.** The live device registry and the Telegram
  secrets are not in any package, so upgrades can't overwrite them.

## Past security issues

See the repository's **Security → Advisories** page.
