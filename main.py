"""KangEdit entry point:  python main.py [project.kgproj | video files...]"""

import sys

from kangedit.app import main

if __name__ == "__main__":
    sys.exit(main())
