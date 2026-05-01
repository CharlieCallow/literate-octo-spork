.PHONY: render help

help:
	@echo "Targets:"
	@echo "  make render REPORT=<dir>   Re-render a report PDF from <dir>/draft.md."
	@echo "                             Example: make render REPORT=reports/42"

render:
	@if [ -z "$(REPORT)" ]; then echo "usage: make render REPORT=<dir>"; exit 2; fi
	python -m scripts.render_report $(REPORT)
