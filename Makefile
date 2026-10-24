.PHONY: help install login check demo test lint run once docker-build docker-up docker-logs

help:
	@echo "install      – venv তৈরি করে ডিপেন্ডেন্সি ইনস্টল করবে"
	@echo "login        – ইউজারবট লগইন (OTP সহ, লোকাল মেশিনে চালান)"
	@echo "check        – কনফিগ, AI, বট ও সোর্স যাচাই"
	@echo "demo         – ক্রেডেনশিয়াল ছাড়াই অফলাইন ডেমো রান"
	@echo "test         – টেস্ট চালাবে"
	@echo "run          – সার্ভিস চালু (বট + শিডিউলার)"
	@echo "once         – একবার স্ক্যান (cron-এর জন্য)"
	@echo "docker-up    – Docker Compose দিয়ে চালু"

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"
	@test -f .env || cp .env.example .env && echo "→ .env তৈরি হয়েছে, মান বসান"

login:
	.venv/bin/autopost login

check:
	.venv/bin/autopost check

demo:
	.venv/bin/autopost demo --output data/demo_preview.html
	@echo "→ data/demo_preview.html ব্রাউজারে খুলুন"

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check src tests

run:
	.venv/bin/autopost run

once:
	.venv/bin/autopost once --dry-run

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-logs:
	docker compose logs -f --tail=100
