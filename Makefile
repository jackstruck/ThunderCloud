.DEFAULT_GOAL := help
.PHONY: help check-release publish-console publish-gpu publish-acquisition

# Run from the repository root. Obtain IMAGE_PREFIX from Terraform's
# artifact_registry_image_prefix output; RELEASE should be a unique tag.
help:
	@echo 'Release targets: publish-console, publish-gpu, publish-acquisition'
	@echo 'Set IMAGE_PREFIX and RELEASE; acquisition also requires CONSOLE_IMAGE at a digest.'
	@echo 'See data-batch-gcp-scaffold/OPERATIONS.md for deployment and Terraform steps.'

check-release:
	@test -n "$(IMAGE_PREFIX)" || { echo 'Set IMAGE_PREFIX to the Artifact Registry image prefix.' >&2; exit 1; }
	@test -n "$(RELEASE)" || { echo 'Set RELEASE to a unique image tag.' >&2; exit 1; }

publish-console: check-release
	docker build --platform linux/amd64 -f data-batch-gcp-scaffold/Dockerfile.console -t "$(IMAGE_PREFIX)/console:$(RELEASE)" data-batch-gcp-scaffold
	docker push "$(IMAGE_PREFIX)/console:$(RELEASE)"

publish-gpu: check-release
	docker build --platform linux/amd64 -f data-batch-gcp-scaffold/Dockerfile -t "$(IMAGE_PREFIX)/worker:$(RELEASE)" data-batch-gcp-scaffold
	docker push "$(IMAGE_PREFIX)/worker:$(RELEASE)"

publish-acquisition: check-release
	@test -n "$(CONSOLE_IMAGE)" || { echo 'Set CONSOLE_IMAGE to the released console image at an immutable digest.' >&2; exit 1; }
	@case "$(CONSOLE_IMAGE)" in "$(IMAGE_PREFIX)"/console@sha256:*) ;; *) echo 'CONSOLE_IMAGE must be a console digest in IMAGE_PREFIX.' >&2; exit 1;; esac
	@printf '%s\n' "$(CONSOLE_IMAGE)" | grep -Eq '@sha256:[0-9a-f]{64}$$' || { echo 'CONSOLE_IMAGE needs a 64-character SHA-256 digest.' >&2; exit 1; }
	docker build --platform linux/amd64 -f data-batch-gcp-scaffold/Dockerfile.acquisition --build-arg "CONSOLE_IMAGE=$(CONSOLE_IMAGE)" -t "$(IMAGE_PREFIX)/acquisition-batch:$(RELEASE)" .
	docker push "$(IMAGE_PREFIX)/acquisition-batch:$(RELEASE)"
