# PocketBolt hosted test deployment

This branch adds a Render Blueprint so the existing Flask application can be deployed as a small **test-mode** web service. It also stages the browser assets: the repository currently stores `index.html`, `app.js`, and related files in the repository root, but `app.py` serves those paths from `static/`.

## Start deployment

1. Open the Render Blueprint deployment flow: https://render.com/deploy?repo=https://github.com/followmecloselyprosperity777-hue/Pre-trial-Release-Services-/tree/pocketbolt-android-deploy
2. Sign in to Render and authorize access to the repository if prompted.
3. Review the Blueprint and choose **Apply** to create the service. This step requires your Render account; I cannot accept provider terms or create an account on your behalf.
4. Wait for the deploy and open the `onrender.com` service URL Render assigns. The `/healthz` route reports service health.

The Blueprint requests Render's free web-service plan. Free instances may sleep when idle, and **the SQLite database is placed under `/tmp` and is ephemeral**: accounts and simulated activity may be reset when the service restarts or redeploys. This is deliberately a prototype deployment, not a durable wallet. For durable persistence, attach a paid persistent disk and change `POCKETBOLT_DB` to a file path under its mount point before inviting testers.

On startup, `seed.py` creates demo accounts if they do not already exist. Demo credentials are `denzel`, `maya`, or `jordan` with password `demo1234`. New signups also work while the instance is running. The service uses a generated Flask session secret and HTTPS-only cookies. No live Plaid or Stripe keys are configured; all payment behavior stays in test mode.

**Do not use real card or bank credentials, and do not treat simulated balances as real money.** This prototype is not approved or equipped for real-money transfers. A production financial service requires substantial legal, security, compliance, banking, and operational work described in the project README.

## Local check

```sh
python -m pip install -r requirements.txt
python prepare_deploy.py
python -m pytest -q
```
