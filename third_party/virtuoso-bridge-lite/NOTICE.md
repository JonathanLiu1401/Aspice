# Vendored virtuoso-bridge-lite

Upstream: https://github.com/Arcadia-1/virtuoso-bridge-lite (MIT, see LICENSE).

`src/virtuoso_bridge` is the package aspice's sign-off (`lib/aspverify`) imports.
Pinned at commit `faef99b`, the revision `lib/aspverify` imports (`cadence_env` and the SSH transport). The live-session
notes in `references/virtuoso/` are the bridge skill's reference pages copied in so
this repository does not symlink anyone's home directory.

If `virtuoso_bridge` is already installed, that install is used. The vendored tree
is only added to `sys.path` when the import would otherwise fail.
