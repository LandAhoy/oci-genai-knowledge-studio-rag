import { useCallback } from 'react';

export function useOpenDocument() {
  const openDocument = useCallback(() => {
    window.open(
      'https://www.oracle.com/cloud/',
      '_blank',
    );
  }, []);

  return openDocument;
}
