# Generic error deployment and repository naming

## Deployed error copy

The invalid video-page message is now:

> Use a direct video page link. User and search pages are not supported here.

Built with Cloud Build `fdf897e2-8e0e-42cc-bcbc-e18aecb44943` by applying only the
message replacement to the previous deployed console image. No profile importer or
enrollment job was added or started.

- Console image: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:e9b040f53370c7d5af10207cc2cdcc8de3387afc4557ccdc8a240358c5dfc4f4`
- Ready revision: `face-console-00041-tp4`, 100% latest-revision traffic, IAP enabled.
- Verified the ready revision's image through the Cloud Run API. Registry cleanup
  pins preserve the new image and its predecessor; local Terraform image settings
  reflect the deployment.

## Data naming release and documentation cleanup

Renamed the application directory to `data-batch-gcp-scaffold`, retaining the old
path as a compatibility symlink. Updated package branding, command names, UI labels,
and current documentation. Database and public API identifiers, runtime environment
variables, deployed resource names, migrations, model names, and historical evidence
remain compatible. The console naming changes are now deployed.

See [naming compatibility](../naming-compatibility.md) and
[cleanup candidates](../cleanup-candidates.md). Following user approval, 10 obsolete documentation and completed selection files
were deleted; archived Python scripts remain. Navigation links were checked after
removal.

Validation: 40 targeted Python tests, 16 Node DOM/import tests (no Chromium), a
successful wheel build with all new and legacy command targets checked, shell
syntax validation, and verification of the directory compatibility symlink.

The naming release was built with Cloud Build
`eb589aaf-512e-482c-86b9-0ff63416c4c5` and deployed to console revision
`face-console-00042-vm8`. The Cloud Run API confirmed Ready, 100% latest-revision
traffic, IAP enabled, and the expected image:
`us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:7618b710264d2727b172af908bcd079dd0509cd39bbe9857c32b3d30fb3200ea`.

The image layers the UI, console wording, and package/command naming changes onto
the previous deployed console image. Worker jobs and database identifiers were
unchanged. Registry pins and local Terraform console-image settings were updated.

## Removal of the local compatibility directory

At the user's request, the `face-batch-gcp-scaffold` symlink was removed after
updating active local absolute paths to `data-batch-gcp-scaffold`. Environment
configuration, virtual-environment launchers/activation files, and local utility
scripts were updated. The legacy editable distribution was replaced with
`thundercloud-data-batch` installed from the new directory. Historical logs and
receipts retain their original recorded paths. No cloud deployment was required.
