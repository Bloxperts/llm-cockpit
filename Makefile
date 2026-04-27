# Build targets for llm-cockpit. Sprint 4 wheel-release runbook.
#
# Common entry points:
#   make build-frontend   — `next build` and copy to src/cockpit/frontend_dist
#   make build            — frontend + python -m build (sdist + wheel)
#   make release          — print release-publish reminders
#   make smoke            — pytest the full suite
#   make clean            — remove build artefacts
#
# `make build-frontend` is also runnable as `bash scripts/build-frontend.sh`.
.PHONY: build-frontend build release smoke clean

build-frontend:
	bash scripts/build-frontend.sh

build: build-frontend
	python -m build

release: build
	@echo ""
	@echo "Wheel and sdist built in dist/. Next steps:"
	@echo "  1. python -m venv /tmp/test-cockpit-venv && \\"
	@echo "     source /tmp/test-cockpit-venv/bin/activate && \\"
	@echo "     pip install dist/llm_cockpit-*.whl && cockpit-admin --version"
	@echo "  2. git tag v<X.Y.Z>"
	@echo "  3. git push origin v<X.Y.Z>"
	@echo "  4. gh release create v<X.Y.Z> dist/llm_cockpit-*"
	@echo ""
	@echo "Or install from GitHub directly without uploading:"
	@echo "  pip install git+https://github.com/Bloxperts/llm-cockpit.git@v<X.Y.Z>"

smoke:
	pytest tests/ -q

clean:
	rm -rf dist/ build/ frontend/out frontend/.next src/cockpit.egg-info
