import os

# The website worker looks up company domains over the internet; tests never do.
os.environ.setdefault("HBF_WEBSITE_LOOKUP", "0")
