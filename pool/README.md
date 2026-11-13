# pool/

Imported packages, one directory per architecture:

    pool/<architecture>/<package>_<version>_<architecture>.deb
    pool/<architecture>/<package>_<version>_<architecture>.deb.provenance.json

Do not add, rename or replace files here by hand. Use `bin/hcgrepo import-deb` (or the GitHub
import commands) and `bin/hcgrepo withdraw`; they keep `repo-lock.json` consistent. Packages are
ordinary Git blobs: never Git LFS (see `.gitattributes`). GitHub rejects files over 100 MB.

Empty: no package has been published yet.
