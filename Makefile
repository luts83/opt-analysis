# opt-analysis — 어디서든 같은 명령으로 개발
.PHONY: setup doctor report report-preview bot weekly weekly-preview test help

PYTHON ?= .venv/bin/python

help:
	@echo "Targets:"
	@echo "  make setup            새 컴퓨터 환경 구성"
	@echo "  make doctor           환경 점검"
	@echo "  make report           일일 리포트 (저장)"
	@echo "  make report-preview   일일 리포트 (저장 안 함)"
	@echo "  make bot              텔레그램 봇"
	@echo "  make weekly           주간 검증"
	@echo "  make weekly-preview   주간 검증 (저장/메일 없음)"
	@echo "  make test             단위 테스트"

setup:
	./scripts/setup.sh

doctor:
	./scripts/doctor.sh

report: doctor
	$(PYTHON) main.py

report-preview:
	$(PYTHON) main.py --no-save

bot:
	$(PYTHON) bot.py

weekly:
	$(PYTHON) weekly.py

weekly-preview:
	$(PYTHON) weekly.py --no-email --no-save

test:
	$(PYTHON) test_expiry_selector.py
	$(PYTHON) test_price_levels.py
