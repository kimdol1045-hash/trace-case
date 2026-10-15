"""Object-storage and attachment safety errors independent from a vendor."""


class ObjectStorageError(RuntimeError):
    pass


class ObjectStorageSetupRequired(ObjectStorageError):
    pass


class ObjectStorageTransportError(ObjectStorageError):
    pass


class ObjectNotFound(ObjectStorageTransportError):
    pass


class AttachmentValidationError(ObjectStorageError):
    """An uploaded object failed a deterministic attachment safety check."""


class UnsafeFilename(AttachmentValidationError):
    pass


class FileTooLarge(AttachmentValidationError):
    pass


class FileSizeMismatch(AttachmentValidationError):
    pass


class MimeTypeNotAllowed(AttachmentValidationError):
    pass


class MimeContentMismatch(AttachmentValidationError):
    pass


class ChecksumMismatch(AttachmentValidationError):
    pass


class MalwareDetected(AttachmentValidationError):
    pass


class MalwareScannerUnavailable(ObjectStorageError):
    pass


class LegacyAttachmentMigrationRequired(ObjectStorageError):
    pass
