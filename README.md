# Cult Product Health

A dashboard and AI pipeline for Cult Massagers and Scales. It tracks Amazon and other e-commerce reviews and ratings, analyses the return and exchange data from Google Sheets, and runs a judge step so every number shown can be verified against its source.

See [PLAN.md](./PLAN.md) for the full plan: architecture, data model, the accuracy and judge design, the 4.1 rating calculator, phases, and open questions.

> **Data policy:** raw sheets and exports contain customer PII and must never be committed. `.gitignore` blocks `*.xlsx`, `*.csv` and `data/`.
