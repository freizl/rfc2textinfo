VENV := $(HOME)/Downloads/github/xml2rfc/.venv
PYTHON := $(VENV)/bin/python3

.PHONY: all sync clean

# Convert all specs listed in specs.conf
all: sync

sync:
	$(PYTHON) rfc2texi.py

# Convert a single file: make convert FILE=xml/rfc9126.xml
convert:
	$(PYTHON) rfc2texi.py $(FILE)

# Remove generated .texi, .info, and dir files
clean:
	rm -f *.texi *.info dir
