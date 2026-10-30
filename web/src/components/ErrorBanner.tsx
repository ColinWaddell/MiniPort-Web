interface Props {
  text: string;
  onDismiss?: () => void;
}

export function ErrorBanner({ text, onDismiss }: Props) {
  return (
    <div className="banner">
      <span>⚠ {text}</span>
      <button className="link" onClick={onDismiss}>
        dismiss
      </button>
    </div>
  );
}