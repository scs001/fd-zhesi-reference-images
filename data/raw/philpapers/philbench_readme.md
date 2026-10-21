# PhilBench

An interactive dashboard comparing the philosophical views of large language
models to the consensus of professional philosophers, using the 100 questions of
the [2020 PhilPapers Survey](https://survey2020.philpeople.org/) (David Bourget &
David J. Chalmers; 1,785 respondents).

Each model answers all 100 questions across multiple prompt variants and runs; the
dashboard lets you browse per-model and per-question results, track trends over
release date and capability, and compare model answers to the philosopher baseline.

## Hosting

This repository hosts the static dashboard via GitHub Pages. `index.html` lazily
fetches its data from `data/*.json`, so it must be served over HTTP (it will not
work opened directly from `file://`). GitHub Pages serves it over HTTPS, which
satisfies that requirement.

```
index.html              # dashboard shell
data/core.json          # per-model / per-question results (loaded on start)
data/consistency.json   # View Consistency data (loaded on demand)
data/variants.json      # Prompt Variants data (loaded on demand)
```

Eval code and the full response data bundles live separately.
