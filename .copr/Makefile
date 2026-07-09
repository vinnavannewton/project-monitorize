SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

NAME := monitorize
SPEC := monitorize.spec
VERSION := $(shell awk '/^Version:/ {print $$2; exit}' $(SPEC))
outdir ?= .
rpmbuilddir := $(abspath $(outdir))/rpmbuild

print-version:
	@printf '%s\n' "$(VERSION)"

srpm:
	test -n "$(VERSION)"
	mkdir -p $(outdir) $(rpmbuilddir)/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS,tmp}
	git archive --format=tar --prefix=$(NAME)-$(VERSION)/ HEAD \
		pyproject.toml $(SPEC) packaging linux README.md LICENSE COMMERCIAL_LICENSE.md \
		| gzip -9 > $(outdir)/$(NAME)-$(VERSION).tar.gz
	rpmbuild -bs $(SPEC) \
		--define "_topdir $(rpmbuilddir)" \
		--define "_sourcedir $(abspath $(outdir))" \
		--define "_srcrpmdir $(abspath $(outdir))" \
		--define "_tmppath $(rpmbuilddir)/tmp"
