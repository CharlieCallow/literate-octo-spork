# Forte Research

Multi-agent thematic research reports. See [PROJECT.md](./PROJECT.md) for the spec.

## Local dev

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY, FRED_API_KEY, DASHBOARD_PASSWORD_HASH, SESSION_SECRET
docker-compose up --build
```

- API: http://localhost:8000
- Dashboard: http://localhost:3000
- Postgres: localhost:5432 (user/pass `forte`/`forte`)

## Generate a password hash

```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'mypassword', bcrypt.gensalt()).decode())"
```

## Smoke test (M1)

```bash
curl -u admin:mypassword -X POST http://localhost:8000/reports \
  -H 'content-type: application/json' \
  -d '{"theme": "the nuclear renaissance, sized"}'
```

Watch progress in the dashboard. PDF lands in `reports/<id>/report.pdf`.

## Layout

See [PROJECT.md §4](./PROJECT.md) and [CLAUDE.md](./CLAUDE.md).
