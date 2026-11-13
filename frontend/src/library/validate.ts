export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export const ALLOWED_EXTENSIONS = ["pdf", "docx", "txt", "md"] as const;

/** Client-side pre-check (the server stays authoritative). Returns a message in words, or null when fine. */
export function validateFile(file: { name: string; size: number }): string | null {
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  if (!(ALLOWED_EXTENSIONS as readonly string[]).includes(ext)) {
    return `${file.name}: only PDF, DOCX, TXT and MD files are supported.`;
  }
  if (file.size === 0) return `${file.name}: the file is empty.`;
  if (file.size > MAX_UPLOAD_BYTES) {
    const mb = (file.size / 1024 / 1024).toFixed(1);
    return `${file.name}: ${mb} MB is over the 25 MB limit.`;
  }
  return null;
}
