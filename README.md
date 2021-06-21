how to use this
===============

- `pip3 install -r requirements.txt`
- copy `config.py.example` to `config.py`
- run:
  ```
aerich init-db
   ```
- run `python3 -m app`

migrations
==========

- init: `aerich init -t app.aerich.TORTOISE_ORM`
- init db: `aerich init-db`
- make migration: `aerich migrate --name migration_name`
- upgrade db schema: `aerich upgrade`
- downgrade: `aerich downgrade [-v version]`
- history: `aerich history`
- migrations remaining: `aerich heads`
