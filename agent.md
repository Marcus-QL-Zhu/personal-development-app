# Aweson桌游助手 Agent Notes

## Public Repository Notes

This file is intentionally kept free of private deployment details.

Do not commit:

- real server IPs, domains, usernames, SSH commands, or firewall rules
- real `.env` values, API tokens, or account-bound voice IDs
- runtime data, uploaded files, generated TTS audio, APKs, or deployment archives

## Local Development

Use `.env.example` as the public template and keep the real `.env` outside version
control. Backend and mobile commands are documented in `tools/README.md` and the
project docs.

## Private Deployment Notes

Keep production deployment instructions in a private note or password manager. The
public repo should only document generic deployment shape, for example:

- run the FastAPI backend with uvicorn
- keep `.env` and runtime data outside source control
- protect non-health endpoints with `GAMEVOICE_PUBLIC_API_TOKEN`
- use HTTPS/WSS for long-term public mobile access
