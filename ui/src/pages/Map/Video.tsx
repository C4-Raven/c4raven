/** Video camera glyph for video sensor points; rendered to static markup for a Leaflet div-icon. */
export default function Video() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width="100%"
      height="100%"
      fill="#BEBEBE"
      stroke="#000000"
      strokeWidth={1}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x={3} y={6} width={12} height={12} rx={2} ry={2} />
      <path d="M15 10 L21 7 L21 17 L15 14 Z" />
    </svg>
  );
}
