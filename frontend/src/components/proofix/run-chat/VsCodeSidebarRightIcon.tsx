export function VsCodeSidebarRightIcon({
  active = false,
  className = "h-4 w-4",
}: {
  active?: boolean;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 16 16"
      className={className}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      {/* Outer rounded container border */}
      <rect
        x="1.5"
        y="2"
        width="13"
        height="12"
        rx="2.5"
        stroke="currentColor"
        strokeWidth="1.25"
      />
      {/* Center vertical partition line */}
      <line
        x1="8.5"
        y1="2"
        x2="8.5"
        y2="14"
        stroke="currentColor"
        strokeWidth="1.25"
      />
      {/* Right partition solid fill matching right rounded corners — only when active */}
      {active && (
        <path
          d="M8.5 2.5H12C13.1 2.5 14 3.4 14 4.5V11.5C14 12.6 13.1 13.5 12 13.5H8.5V2.5Z"
          fill="currentColor"
        />
      )}
    </svg>
  );
}
