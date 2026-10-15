"""Private S3-compatible object storage primitives for R2 and local MinIO."""

from tc_backend.infrastructure.object_storage.adapter import (
    ObjectMetadata,
    S3CompatibleObjectStorage,
    StoredObject,
    StoredObjectPage,
)
from tc_backend.infrastructure.object_storage.errors import (
    AttachmentValidationError,
    ChecksumMismatch,
    FileSizeMismatch,
    FileTooLarge,
    LegacyAttachmentMigrationRequired,
    MalwareDetected,
    MalwareScannerUnavailable,
    MimeContentMismatch,
    MimeTypeNotAllowed,
    ObjectNotFound,
    ObjectStorageError,
    ObjectStorageSetupRequired,
    ObjectStorageTransportError,
    UnsafeFilename,
)
from tc_backend.infrastructure.object_storage.scanner import (
    ClamAVInstreamScanner,
    MalwareScanner,
    ScanResult,
    ScanVerdict,
)
from tc_backend.infrastructure.object_storage.validation import (
    VerifiedObject,
    attachment_content_disposition,
    normalize_filename,
    validate_declaration,
    verify_quarantined_object,
)

__all__ = [
    "ObjectMetadata",
    "AttachmentValidationError",
    "ChecksumMismatch",
    "FileSizeMismatch",
    "FileTooLarge",
    "LegacyAttachmentMigrationRequired",
    "MalwareDetected",
    "MalwareScannerUnavailable",
    "MimeContentMismatch",
    "MimeTypeNotAllowed",
    "ObjectNotFound",
    "ObjectStorageError",
    "ObjectStorageSetupRequired",
    "ObjectStorageTransportError",
    "S3CompatibleObjectStorage",
    "StoredObject",
    "StoredObjectPage",
    "UnsafeFilename",
    "ClamAVInstreamScanner",
    "MalwareScanner",
    "ScanResult",
    "ScanVerdict",
    "VerifiedObject",
    "attachment_content_disposition",
    "normalize_filename",
    "validate_declaration",
    "verify_quarantined_object",
]
