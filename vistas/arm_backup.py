#!/usr/bin/env python
"""Encrypted, incremental, OFF-MACHINE backup of arm_repo/ (the LICENSED LSEG StarMine ARM
parquet) — so the one piece that can NEVER go to GitHub still survives a local disk crash.

WHY a separate path (not the vistas-codebase git backup): arm_repo/ is paid third-party IP
(~1.1 GB and growing weekly) — it must NEVER be committed or published. So instead of git we
back it up to a CLOUD DRIVE, and we ENCRYPT every file locally first, so the cloud provider
(e.g. OneDrive/Microsoft) only ever stores CIPHERTEXT — never the readable licensed data.

WHAT it does — an incremental ENCRYPTED MIRROR:
  - walks arm_repo/ (skipping the regenerable compiled/ cache — rebuilt on restore),
  - encrypts each file INDEPENDENTLY with AES-256-GCM (a fresh random 12-byte nonce per file;
    the file's relative path is authenticated as AAD so a file can't be silently swapped),
  - writes <target>/data/<relpath>.enc, and records size+mtime+SHA-256 in <target>/_manifest.json,
  - on later runs only NEW/CHANGED files are re-encrypted (compared by size+mtime), so after the
    first ~1.1 GB run each weekly refresh uploads only the small new drop.

KEY — the make-or-break for crash safety. The cloud copy is recoverable ONLY if the KEY
survives the SAME crash, so the secret must live OFF this machine:
  - PREFERRED: set env  VISTAS_ARM_BACKUP_PASSPHRASE  to a strong passphrase you keep in your
    password manager (off-machine by nature). The 32-byte key is scrypt-derived from it.
  - ELSE: a random 32-byte keyfile is auto-created at  ~/.vistas/arm_backup.key  — and you MUST
    copy it somewhere off this machine. If this disk dies and the key was only here, the backup
    CANNOT be decrypted. (The tool prints the key once, loudly, when it first creates it.)

TARGET dir:  env  VISTAS_ARM_BACKUP_DIR  else  <OneDrive>/VistasBackups/arm_repo.

CLI:
    python -m vistas.arm_backup                  # incremental encrypted backup
    python -m vistas.arm_backup --verify         # check the mirror is complete + decryptable
    python -m vistas.arm_backup --restore <DEST> # rebuild an arm_repo/ tree from the mirror
                                                 #   then: python -c "from vistas import arm; arm.compile_india()"
"""
from __future__ import annotations

import os
import sys
import json
import base64
import hashlib
import argparse
import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

ARM_DIR = os.environ.get("VISTAS_ARM_DIR", os.path.join(_ROOT, "arm_repo"))
EXCLUDE_TOP = {"compiled"}        # regenerable cache — rebuilt via arm.compile_india() after restore
MAGIC = b"VARM1\n"                # file header tag so we can sanity-check a ciphertext file
KEYFILE = os.environ.get("VISTAS_ARM_BACKUP_KEYFILE",
                         os.path.join(os.path.expanduser("~"), ".vistas", "arm_backup.key"))


# ----------------------------------------------------------------------- small helpers
def _say(m=""):
    print(m, flush=True)


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, p)


def _target_dir():
    """Where the encrypted mirror lives: VISTAS_ARM_BACKUP_DIR, else <OneDrive>/VistasBackups/arm_repo."""
    t = os.environ.get("VISTAS_ARM_BACKUP_DIR")
    if t:
        return os.path.abspath(t)
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        od = os.environ.get(env)
        if od and os.path.isdir(od):
            return os.path.join(od, "VistasBackups", "arm_repo")
    return None


# ----------------------------------------------------------------------- key handling
def _resolve_key(meta, create=True):
    """Return (key32, meta). Passphrase (scrypt) if env set; else a random keyfile.

    `create=False` (restore/verify) refuses to mint a new key — you must supply the original
    passphrase or restore the keyfile, otherwise the backup is (correctly) unreadable."""
    pw = os.environ.get("VISTAS_ARM_BACKUP_PASSPHRASE")
    if pw:
        salt_b64 = meta.get("salt")
        if not salt_b64:
            if not create:
                raise SystemExit("  cannot derive key: no salt stored in the target _meta.json.")
            salt = os.urandom(16)
            meta["salt"] = base64.b64encode(salt).decode()
        else:
            salt = base64.b64decode(salt_b64)
        kdf = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1)
        return kdf.derive(pw.encode("utf-8")), meta

    if os.path.exists(KEYFILE):
        with open(KEYFILE, encoding="utf-8") as f:
            return base64.b64decode(f.read().strip()), meta

    if not create:
        raise SystemExit(f"  no key available. Set VISTAS_ARM_BACKUP_PASSPHRASE or restore the "
                         f"keyfile at {KEYFILE}, then retry.")

    os.makedirs(os.path.dirname(KEYFILE), exist_ok=True)
    key = os.urandom(32)
    with open(KEYFILE, "w", encoding="utf-8") as f:
        f.write(base64.b64encode(key).decode())
    try:
        os.chmod(KEYFILE, 0o600)
    except Exception:
        pass
    _say("=" * 72)
    _say("  A NEW ENCRYPTION KEY WAS CREATED FOR THE ARM BACKUP:")
    _say(f"    file: {KEYFILE}")
    _say(f"    key : {base64.b64encode(key).decode()}")
    _say("  >>> COPY THIS KEY OFF THIS MACHINE NOW (password manager / USB / another cloud).")
    _say("  >>> If this disk dies and the key was ONLY here, the backup CANNOT be decrypted.")
    _say("  (Tip: instead set env VISTAS_ARM_BACKUP_PASSPHRASE to a passphrase you already keep")
    _say("   in your password manager — then there is no keyfile to lose.)")
    _say("=" * 72)
    return key, meta


def _key_id(key):
    return hashlib.sha256(key).hexdigest()[:16]


# ----------------------------------------------------------------------- per-file crypto
def _encrypt_file(src, dst, key, relpath):
    aes = AESGCM(key)
    nonce = os.urandom(12)
    with open(src, "rb") as f:
        pt = f.read()
    ct = aes.encrypt(nonce, pt, relpath.encode("utf-8"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(MAGIC)
        f.write(nonce)
        f.write(ct)
    os.replace(tmp, dst)
    return len(pt), hashlib.sha256(pt).hexdigest(), os.path.getsize(dst)


def _decrypt_blob(blob, key, relpath):
    if blob[:len(MAGIC)] != MAGIC:
        raise ValueError("bad header (not a VARM ciphertext file)")
    nonce = blob[len(MAGIC):len(MAGIC) + 12]
    ct = blob[len(MAGIC) + 12:]
    return AESGCM(key).decrypt(nonce, ct, relpath.encode("utf-8"))


# ----------------------------------------------------------------------- source walk
def _source_files():
    out = []
    for dp, _dn, fn in os.walk(ARM_DIR):
        rel0 = os.path.relpath(dp, ARM_DIR)
        if rel0 != "." and rel0.split(os.sep)[0] in EXCLUDE_TOP:
            continue
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, ARM_DIR).replace(os.sep, "/")
            out.append((p, rel))
    return sorted(out, key=lambda x: x[1])


# ----------------------------------------------------------------------- commands
def backup():
    if not os.path.isdir(ARM_DIR):
        _say(f"  arm_repo not found at {ARM_DIR} — nothing to back up.")
        return 1
    target = _target_dir()
    if not target:
        _say("  NO BACKUP TARGET. Set VISTAS_ARM_BACKUP_DIR to a cloud-synced folder (or sign")
        _say("  in to OneDrive). Aborting — refusing to back up nowhere.")
        return 1

    data_dir = os.path.join(target, "data")
    os.makedirs(data_dir, exist_ok=True)
    meta_path = os.path.join(target, "_meta.json")
    man_path = os.path.join(target, "_manifest.json")

    meta = _load_json(meta_path, {})
    key, meta = _resolve_key(meta, create=True)
    kid = _key_id(key)
    if meta.get("key_id") and meta["key_id"] != kid:
        _say("  KEY MISMATCH — the key/passphrase here does not match the one this backup was")
        _say("  made with. Refusing to write (it would corrupt the mirror). Use the original key.")
        return 1
    meta["key_id"] = kid
    meta["algo"] = "AES-256-GCM; scrypt(n=2^15) if passphrase"
    meta.setdefault("created", _now())

    man = _load_json(man_path, {"files": {}})
    files = man.get("files", {})

    srcs = _source_files()
    n_new = n_upd = n_skip = 0
    bytes_enc = 0
    for p, rel in srcs:
        st = os.stat(p)
        prev = files.get(rel)
        enc_path = os.path.join(data_dir, rel + ".enc")
        if (prev and prev.get("size") == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns
                and os.path.exists(enc_path)):
            n_skip += 1
            continue
        size, sha, enc_size = _encrypt_file(p, enc_path, key, rel)
        files[rel] = {"size": size, "mtime_ns": st.st_mtime_ns, "sha256": sha, "enc": enc_size}
        bytes_enc += size
        if prev:
            n_upd += 1
        else:
            n_new += 1
        if (n_new + n_upd) % 100 == 0:
            _say(f"    … {n_new + n_upd} files encrypted ({bytes_enc / 1e6:.0f} MB)")

    src_set = {rel for _p, rel in srcs}
    orphans = [r for r in files if r not in src_set]   # gone from source — KEPT (append-only safety)

    man["files"] = files
    man["updated"] = _now()
    man["source"] = ARM_DIR
    _save_json(meta_path, meta)
    _save_json(man_path, man)

    total_mb = sum(v["size"] for v in files.values()) / 1e6
    _say(f"  backup OK  ->  {target}")
    _say(f"    this run: {n_new} new, {n_upd} updated, {n_skip} unchanged; "
         f"{bytes_enc / 1e6:.0f} MB encrypted")
    _say(f"    mirror now holds {len(files)} files (~{total_mb:.0f} MB plaintext-equivalent)"
         + (f"; {len(orphans)} orphan(s) kept" if orphans else ""))
    if "onedrive" in target.lower():
        _say("    OneDrive will upload the new ciphertext to the cloud in the background.")
    _say("  KEY reminder: make sure the key is saved OFF this machine (passphrase in your")
    _say(f"  password manager, or a copy of {KEYFILE}) — without it the backup can't be restored.")
    return 0


def verify():
    target = _target_dir()
    if not target or not os.path.isdir(target):
        _say(f"  no backup found (target = {target}).")
        return 1
    man = _load_json(os.path.join(target, "_manifest.json"), None)
    meta = _load_json(os.path.join(target, "_meta.json"), {})
    if not man:
        _say("  no _manifest.json in the target.")
        return 1
    key, _ = _resolve_key(meta, create=False)
    if meta.get("key_id") and meta["key_id"] != _key_id(key):
        _say("  WRONG KEY — does not match this backup.")
        return 1
    data_dir = os.path.join(target, "data")
    files = man["files"]
    miss = bad = sampled = 0
    items = sorted(files.items())
    for i, (rel, info) in enumerate(items):
        enc = os.path.join(data_dir, rel + ".enc")
        if not os.path.exists(enc):
            miss += 1
            continue
        # spot-decrypt a sample (first/last few + every ~250th) to prove the key + integrity,
        # without paying to decrypt all ~1.1 GB on every check
        if i < 5 or i >= len(items) - 5 or i % 250 == 0:
            sampled += 1
            try:
                with open(enc, "rb") as f:
                    pt = _decrypt_blob(f.read(), key, rel)
                if hashlib.sha256(pt).hexdigest() != info["sha256"]:
                    bad += 1
                    _say(f"    CHECKSUM MISMATCH: {rel}")
            except Exception as e:
                bad += 1
                _say(f"    DECRYPT FAILED: {rel} ({e})")
    _say(f"  verify: {len(files)} files in manifest · {miss} missing ciphertext · "
         f"{bad} decrypt/checksum fail (of {sampled} sampled).")
    ok = (miss == 0 and bad == 0)
    _say("  RESULT: " + ("OK — backup is complete and decryptable." if ok else "PROBLEMS — see above."))
    return 0 if ok else 1


def restore(dest):
    target = _target_dir()
    if not target or not os.path.isdir(target):
        _say(f"  no backup found (target = {target}).")
        return 1
    man = _load_json(os.path.join(target, "_manifest.json"), None)
    meta = _load_json(os.path.join(target, "_meta.json"), {})
    if not man:
        _say("  no _manifest.json in the target.")
        return 1
    key, _ = _resolve_key(meta, create=False)
    if meta.get("key_id") and meta["key_id"] != _key_id(key):
        _say("  WRONG KEY — does not match this backup. Aborting.")
        return 1
    data_dir = os.path.join(target, "data")
    dest = os.path.abspath(dest)
    ok = bad = 0
    for rel, info in sorted(man["files"].items()):
        enc = os.path.join(data_dir, rel + ".enc")
        if not os.path.exists(enc):
            _say(f"    MISSING ciphertext: {rel}")
            bad += 1
            continue
        with open(enc, "rb") as f:
            pt = _decrypt_blob(f.read(), key, rel)
        if hashlib.sha256(pt).hexdigest() != info["sha256"]:
            _say(f"    CHECKSUM FAIL: {rel}")
            bad += 1
            continue
        out = os.path.join(dest, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(pt)
        ok += 1
        if ok % 200 == 0:
            _say(f"    … {ok} files restored")
    _say(f"  restore -> {dest}: {ok} OK, {bad} problem(s).")
    _say('  Now rebuild the regenerable cache:  python -c "from vistas import arm; arm.compile_india()"')
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="Encrypted off-machine backup of arm_repo/ (licensed ARM).")
    ap.add_argument("--verify", action="store_true", help="check the mirror is complete + decryptable")
    ap.add_argument("--restore", metavar="DEST", help="rebuild an arm_repo/ tree from the mirror into DEST")
    args = ap.parse_args()

    _say("-" * 72)
    _say("VISTAS — encrypted off-machine backup of arm_repo/ (LICENSED LSEG StarMine ARM)")
    _say("-" * 72)
    if args.restore:
        return restore(args.restore)
    if args.verify:
        return verify()
    return backup()


if __name__ == "__main__":
    sys.exit(main())
