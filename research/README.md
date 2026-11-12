# research/: evaluating ADPO on real CI data

This folder checks whether ADPO's analyzers produce findings that hold up on real
GitHub Actions history, and whether their statistics fit that data.

| File | What it is |
|---|---|
| `repos.yaml` | The repositories and workflows studied, each with a one-line reason |
| `collect.py` | Downloads run/job/step history through ADPO's own fetch layer (`adpo.github_fetch`) |
| `adpo_data.py` | Flattens fetched runs to Parquet tables and rebuilds them as `adpo.ingest` input |
| `evaluation.ipynb` | The analysis; its last cell writes `REPORT.md` and `figures/*.png` |
| `REPORT.md` | The exported results. Every number in it is computed by the notebook |
| `labels.csv` | 40 sampled findings for hand-labelling (`label` = useful / not useful / wrong) |
| `data/collection_meta.json` | Collection date, per-repo run/job/step counts, API requests used |
| `data/sample/` | Small committed sample (30 runs each of 3 repos) so the notebook runs without collecting |
| `data/raw/` | Full collection (gitignored) |

## Reproduce

```bash
cd research
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1. Collect (read-only public API; about 3,500 requests for 21 repos x 150 runs,
#    so a token is required; responses are cached in ~/.cache/adpo, so reruns are free)
GITHUB_TOKEN=<a token with no scopes> python collect.py --runs 150

# 2. Regenerate the notebook outputs, REPORT.md and figures/ (one command)
python -m jupyter nbconvert --to notebook --execute --inplace evaluation.ipynb
```

Without step 1 the notebook falls back to `data/sample/`. The report states which
source it used. The sample is far too small for the report's conclusions; it exists
so the pipeline can be exercised.

The collection is a snapshot. Re-collecting later fetches newer runs, so the numbers
will differ from the committed `REPORT.md`. The cache keeps the original run listings
for as long as it exists.

## After labelling

Fill in the `label` column of `labels.csv` (`useful`, `not useful` or `wrong`; `notes`
is free text), then re-run step 2. The precision cell computes per-analyzer precision
with Wilson 95% intervals, and `REPORT.md` picks the table up.
