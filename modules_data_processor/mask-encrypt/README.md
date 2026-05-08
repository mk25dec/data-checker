# mask-encrypt

## What this is for

Mask, encrypt, or decrypt sensitive columns in data files, with CSV-based auth/key storage.

## CSV-first behavior

- Processed outputs are always written as **CSV**.
- Run report is written as **CSV**.
- Auth + key store is a single **CSV** file:
  - `vault/auth.csv`

## Inputs

- One or more input files via `-i/--input-file` (supports files, directories, and globs).
- Supported input types: `.csv`, `.xlsx`, `.xls`, `.parquet`, `.json`.
- Sensitive columns via `-s/--sensitive-column` or `--sensitive-columns-json`.

## Run

```bash
cd modules_data_processor/mask-encrypt
pixi install

pixi run mask_encrypt -- \
  -i /path/customers.csv \
  -s "pan,account_no" \
  -u user1 \
  --action encrypt
```

Help:

```bash
pixi run help
```

## Notes

- Non-interactive runs can use seeded dev users (`user1`, `admin`, `user0`) if password env var is not set.
- Set `MASK_ENCRYPT_PASSWORD` (or `--password-env-var`) for custom users.
- Legacy split files (`vault/users.csv` + `vault/keys.csv`) are auto-migrated to `vault/auth.csv` on first run.
