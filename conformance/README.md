# Mort executable conformance suite

This directory is the black-box companion to
`docs/language-specification.md`. It tests public compiler behavior rather than
importing Mort's parser or checker internals.

Run it from a source checkout:

```bash
python conformance/run.py
```

Test another compiler command:

```bash
python conformance/run.py --mortc /path/to/mortc
```

The runner supports Python scripts and installed executables. `check` and
`reject` cases need only the frontend. `run` cases also need a native backend.
A conforming hosted implementation must pass every case in the manifest.

New normative behavior must add or update a case and cite the relevant
specification section in `manifest.json`.
