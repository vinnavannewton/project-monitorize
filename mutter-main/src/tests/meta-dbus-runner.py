

import sys

from mutter_dbusrunner import MutterDBusRunner, meta_run

if __name__ == '__main__':
    result = meta_run(MutterDBusRunner)
    sys.exit(result)
