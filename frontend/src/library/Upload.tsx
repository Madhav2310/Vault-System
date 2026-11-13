import { useRef, useState } from "react";
import { ALLOWED_EXTENSIONS } from "./validate";

interface Props {
  busy: boolean;
  onFiles: (files: File[]) => void;
}

export function Upload({ busy, onFiles }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  return (
    <div
      className="dropzone"
      data-over={over}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (e.dataTransfer.files.length) onFiles([...e.dataTransfer.files]);
      }}
    >
      <strong style={{ color: "var(--ink)" }}>Drop files here</strong>
      <span className="hint">PDF, DOCX, TXT or MD, up to 25 MB each</span>
      <label className="btn btn-primary" style={{ cursor: "pointer" }}>
        Choose files
        <input
          ref={input}
          type="file"
          multiple
          className="sr-only"
          accept={ALLOWED_EXTENSIONS.map((e) => `.${e}`).join(",")}
          aria-label="Upload documents"
          disabled={busy}
          onChange={(e) => {
            if (e.target.files?.length) onFiles([...e.target.files]);
            e.target.value = "";
          }}
          onFocus={(e) => (e.target.parentElement!.style.outline = "2px solid var(--focus)")}
          onBlur={(e) => (e.target.parentElement!.style.outline = "")}
        />
      </label>
    </div>
  );
}
