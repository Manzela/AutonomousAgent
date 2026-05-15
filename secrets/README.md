# Secrets

This directory contains sops-encrypted secrets. Plaintext files are gitignored.

**Conventions:**
- Encrypted file names end in `.sops` (e.g. `telegram.env.sops`).
- Decrypt with `sops -d secrets/<name>.sops > secrets/<name>` (do NOT commit the decrypted file).
- Encrypt new secret with `sops -e secrets/<name> > secrets/<name>.sops` then `rm secrets/<name>`.
- The age key lives at `~/.config/sops/age/keys.txt` (Mac host) and must be backed up to a password manager.

**Adding a new secret:**

```bash
# Edit (creates if missing)
sops secrets/new-secret.env.sops
# OR encrypt an existing plaintext file
sops -e secrets/new-secret.env > secrets/new-secret.env.sops
rm secrets/new-secret.env
```

The `sops` command auto-uses the recipient defined in `.sops.yaml`.
