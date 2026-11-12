"""Interpret contract activation history without reconstructing missing proposal bases."""


def activation_id(edge, version):
    return f"contract-activation:{edge}:{version}"


def first_activation_basis(body, other_bodies, *, has_other_activation):
    if body.get("basis_version") is not None or has_other_activation:
        return False
    # A legacy row can represent either a predecessor or an unchosen draft.
    # A separate, explicit proposal basis removes that ambiguity.
    return "basis_version" in body or all("basis_version" in other for other in other_bodies)
