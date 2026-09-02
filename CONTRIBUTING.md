# Contributing

Contributions are welcome. Keep changes focused, avoid committing runtime data
or credentials, and add regression tests for behavior changes where practical.

Before opening a pull request, run:

```bash
python -m pip install -e .
python -m compileall -q src
python -m unittest discover -s tests -v
```

By contributing to this repository, you agree that your contribution may be
distributed under the repository's MIT License.
