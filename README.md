# News Desk

Static news dashboard. A GitHub Actions job (every 30 minutes) runs build.py, which reads config.yml,
fetches feeds, clusters duplicates, scores relevance, writes short AI summaries, and saves docs/data.json.
GitHub Pages serves the docs folder.

Setup once:
1. Repo secret ANTHROPIC_API_KEY (Settings, Secrets and variables, Actions).
2. Settings, Pages, deploy from branch main, folder /docs.
3. Actions tab, run "Build news" once to create the first data.json.

Rules: no case names, client names or matter data in this repo.
