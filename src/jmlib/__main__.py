"""Allow ``python -m jmlib`` without installing the console script."""

from jmlib.cli import main

raise SystemExit(main())
