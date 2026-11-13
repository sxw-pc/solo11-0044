.PHONY: test lint demo

test:
	python -m pytest -q

lint:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check fpsprobe fixtures tests; \
	else \
		python -m compileall -q fpsprobe fixtures tests; \
	fi

demo:
	python -m fpsprobe fixtures gen
	@python -c "import subprocess,sys; p=subprocess.run([sys.executable,'-m','fpsprobe','batch','--config','fixtures/config/default.yaml']); raise SystemExit(0 if p.returncode in (0,1) else p.returncode)"
