# Stack Converter

**Try it: [stack-converter.onrender.com](https://stack-converter.onrender.com/)**

Paste a public Substack article URL. Get the article back as ready-to-paste **LinkedIn Article** and **X Article** formats, with the images, captions and embedded video carried over, and four AI-generated opening hooks per platform that are grounded strictly in the article's own text.

Built for people who write on Substack and republish on LinkedIn and X without wanting to reformat by hand or let an AI rewrite their voice.

## What it does

1. **Fetches the article** from Substack's public API (any author, any publication) and parses the full post: headings, paragraphs, lists, quotes, links, inline images and `<figcaption>` captions.
2. **Rebuilds it for each platform.** LinkedIn Articles and X Articles accept different formatting; the converter produces one version for each, copied to the clipboard with a single button.
3. **Carries media over.** Images are fetched at full resolution; embedded Substack video is served back so it can be re-attached.
4. **Writes hooks, not rewrites.** Four opening hooks per platform (different angles: contrarian, practical, story, takeaway), each under 240 characters, generated from the article text only. Clichés and marketing phrases are banned in the prompt. Any hook can be refreshed individually.
5. **Falls back gracefully.** If no model key is set or the model call fails, hooks are synthesised from the article's own first sentences so the tool still works.

The interface is a single page: one URL field, one Convert button, the article preview, and copy buttons for title, LinkedIn and X.

## Running it locally

Requires Python 3.12+. No third-party Python packages: the server uses only the standard library.

```bash
git clone https://github.com/zahedsaifi94/stack-converter.git
cd stack-converter
export GEMINI_API_KEY=your_key        # optional; see "AI hooks" below
./start.sh                            # serves on http://127.0.0.1:8765
```

Open the URL it prints, paste a Substack link, click Convert.

## Deploying your own

The hosted instance above runs on Render's free plan, so it may take a few seconds to wake up. To run your own copy: the repo includes a `Dockerfile` and a `render.yaml`, so you can [deploy it to Render](https://render.com/deploy?repo=https://github.com/zahedsaifi94/stack-converter) in a few clicks and set `GEMINI_API_KEY` as an environment variable in the Render dashboard. The service listens on `$PORT` (default 8080 in the container) and exposes `/healthz` and `/ping` for uptime monitors, so a free-tier instance can be kept awake with a cron ping.

It will run anywhere that can run a Docker image or a plain `python3 server.py`.

## AI hooks

Hooks are generated with Google's Gemini API by default. Configuration, all via environment variables:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`, `API_KEY`) | Gemini API key from Google AI Studio |
| `GEMINI_MODEL` | Override the model; otherwise the server tries current Gemini 3 models in order |
| `GROQ_API_KEY` / `OPENAI_API_KEY` | Optional. If set (or if a `gsk_`/`sk-` key is pasted into `GEMINI_API_KEY`), the server uses that provider instead |
| `APP_CSRF_SECRET` | Optional. Fixed secret for session tokens; otherwise one is generated and stored in `.csrf_secret` |

Without any key the tool still converts articles; it just uses the text-derived fallback hooks.

## Project layout

```
server.py        the whole backend: Substack fetch, parsing, platform formatting, hook generation, HTTP server
public/          the single-page frontend (index.html, app.js, styles.css)
media_vault/     runtime cache for fetched media (ignored by git)
Dockerfile       container image
render.yaml      one-click Render deployment
start.sh         local launcher
```

## Notes and limits

- Works with **public** Substack posts only; paywalled content behind a subscription is not fetched.
- The converter is deliberately conservative: it reformats, it does not rewrite. The only generated text is the hooks, and they are constrained to the article's content.
- Fetched articles are cached in memory for the life of the process.

## License

MIT. See [LICENSE](LICENSE).
