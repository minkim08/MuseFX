import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = { title: 'MuseFX — Video to SFX', description: 'Turn silent moments into sound.' };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
