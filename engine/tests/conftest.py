"""All pytest filesystem mutations must stay outside repository book libraries."""

import os
from tools.books_guard import install_write_guard

os.environ["BOOKWEB_TEST"] = "1"
install_write_guard()
