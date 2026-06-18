# Atomic Artifact Integrity v0.1

Decision-path artifacts are published as immutable versioned sets.

## Publication flow

1. Create a temporary directory under `decision-paths/sets/` on the target filesystem.
2. Write, flush, and fsync every public graph, Mermaid graph, and restricted audit file.
3. Compute lowercase SHA-256 and byte size for every authoritative artifact.
4. Write `decision-path-manifest.json` last. The manifest never includes its own digest.
5. Verify the complete staged set.
6. Rename the staged directory to `sets/<manifest-sha256>/`.
7. Atomically replace `current.json` with a pointer to the immutable set.

The previous `current.json` remains authoritative until the final pointer replacement. An interrupted staging operation cannot expose a partial set.

## Consumer verification

A consumer must:

1. Parse `current.json`.
2. Verify the referenced manifest SHA-256.
3. Parse the manifest integrity index.
4. Reject missing files, extra files, symbolic links, size mismatches, and hash mismatches.
5. Treat graph and audit JSON as authoritative trial artifacts; the manifest is a derived index and integrity document.

## Security boundary

This protocol provides tamper evidence and complete-set publication. It does not provide digital signatures, signer identity, key management, or protection against an attacker who can rewrite both the artifact set and the trusted pointer.
