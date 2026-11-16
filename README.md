# MRI receive-coil fault detection

Give the tool one headcap session (Siemens raw `.dat` files) and it returns a ranked list of
coil elements that are most likely broken.

- **Summary of the work so far:** [docs/PROJECT_SUMMARY.md](docs/PROJECT_SUMMARY.md)
- **Full technical log:** [.claude/TECHNICAL_LOG.md](.claude/TECHNICAL_LOG.md)
- **Sources:** [docs/References.docx](docs/References.docx)

## Run

```bash
python coil_analysis/channel_fault_analysis.py MPRAGE.dat SNRMAP.dat
```

Outputs (ranking plot, suspect maps, JSON summary) go to `coil_analysis/channel_fault_output/`.

## Tests

```bash
pytest tests/ coil_overlay/test_overlay.py
```

Dependencies: numpy, scipy, matplotlib, twixtools, nibabel, scikit-image, pytest.

Scan data is not included in this repository.
