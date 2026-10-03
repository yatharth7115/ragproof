# Third-party data notices

## Contract Understanding Atticus Dataset (CUAD) v1

Stage 1 uses a deterministic subset of CUAD v1 published by The Atticus
Project. CUAD contains real commercial contracts sourced from SEC EDGAR and
expert-supervised clause annotations.

- Project: https://www.atticusprojectai.org/cuad/
- Source archive: https://github.com/TheAtticusProject/cuad/blob/main/data.zip
- License: Creative Commons Attribution 4.0 International (CC BY 4.0)
- Paper: *CUAD: An Expert-Annotated NLP Dataset for Legal Contract Review*,
  Hendrycks, Burns, Chen, and Ball, NeurIPS 2021.

The tracked fixture is a three-contract deterministic subset. Contract text,
questions, and answer annotations are not rewritten. RAGProof's failure modes
apply labelled transformations at runtime; transformed artifacts are test
outputs and are not represented as original CUAD data.
