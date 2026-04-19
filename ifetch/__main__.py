"""
iFetch main module.

This allows running iFetch commands using:
  python -m ifetch.export_cli
"""

import sys

if __name__ == '__main__':
    # Check if user wants to run export CLI
    if len(sys.argv) > 1 and sys.argv[1] == 'export':
        from .export_cli import main
        sys.exit(main())
    else:
        # Default to original CLI
        from .cli import main
        sys.exit(main())
