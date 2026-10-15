export function isPermanentDeleteConfirmationValid(caseKey: string, confirmation: string) {
  return confirmation === caseKey;
}
