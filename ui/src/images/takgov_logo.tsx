import takLogo from './tak_logo.png';

interface TakGovLogoProps {
  width: number;
  height: number;
}

export function TakGovLogo({ width, height }: TakGovLogoProps) {
  return (
    <img
      src={takLogo}
      width={width}
      height={height}
      alt="TAK"
      style={{ objectFit: 'contain', display: 'block' }}
    />
  );
}

export default TakGovLogo;
