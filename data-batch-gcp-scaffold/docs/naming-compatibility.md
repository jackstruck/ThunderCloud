# Data terminology and compatibility

The current project name is **ThunderCloud data processing**. The application lives
in `data-batch-gcp-scaffold/`. The former `face-batch-gcp-scaffold` compatibility
symlink has been removed. Local environment settings, virtual-environment launchers,
editable package installation, and utility scripts now use the new directory.
Historical logs and receipts retain their original recorded paths.
The Python distribution is `thundercloud-data-batch`.

New command names are `data-worker`, `data-submit`, `data-probe`, `data-console`, and
`data-gallery-correct`. The local development environment has been reinstalled with these entry points.
Other installations should reinstall the package to obtain them. The old
`face-*` entry points remain aliases to the same functions. `source-merges` is unchanged.
The container wrapper invokes the Python module directly, so it also supports images
built before this rename. Its preferred settings are `DATA_WORKER_IMAGE`,
`DATA_ENV_FILE`, and `DATA_RECEIPT_DIR`; the corresponding `FACE_*` settings still work.

This is a terminology change, not a change in processing or matching behavior.
UI selection and result labels use data terminology. Example suppression is labeled
**Not a subject** or **Unusable data**; the stored reason values are unchanged.

The following names deliberately retain compatibility:

- Database names, tables, columns, enum values, and migration contents. Applied
  migrations are checksum tracked and must not be rewritten for a rename.
- API routes and response keys, including `face-groups`, `face-selection`, and
  `representative_faces`, plus persisted run states and suppression reason values.
- Runtime `FACE_*` environment variables, cloud job/service names, IAM identities,
  URLs, bucket object prefixes, image repositories, and Terraform resource identities.
  Renaming these needs a separate coordinated deployment and migration.
- Historical release documents and receipts, which describe the names at the time.
- Technical model names such as AdaFace and descriptions of facial detection or
  embeddings. Branding does not change the type of data being processed.

Existing database identifiers remain compatible as requested. No cloud resources
or stored data were renamed by this repository change.
