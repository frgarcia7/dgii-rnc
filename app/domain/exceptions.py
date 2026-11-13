"""Excepciones propias del dominio."""


class DomainError(Exception):
    """Error base del dominio."""


class ContribuyenteNotFoundError(DomainError):
    """Se lanza cuando no existe un contribuyente para el RNC consultado."""

    def __init__(self, rnc: str) -> None:
        self.rnc = rnc
        super().__init__(f"No se encontró contribuyente con RNC '{rnc}'.")


class InvalidInputError(DomainError):
    """Se lanza cuando un parámetro de dominio es inválido tras normalización."""


class SyncAlreadyRunningError(DomainError):
    """Se lanza cuando se intenta iniciar una sincronización habiendo otra en curso."""

    def __init__(self, message: str = "Ya existe una sincronización en curso.") -> None:
        super().__init__(message)
