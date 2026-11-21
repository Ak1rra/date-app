import { useProfiles } from '../../hooks/useProfiles';
import { personOf } from '../us/options';

export type Person = 'husband' | 'wife';

export interface People {
  partnerName: string;
  mePerson: Person | null;
  partnerPerson: Person | null;
}

/** Who "you" and "your partner" are, for names and person colours (falls back while loading). */
export function usePeople(): People {
  const profiles = useProfiles().data;
  const me = profiles?.find((p) => p.is_me);
  const partner = profiles?.find((p) => !p.is_me);
  return {
    partnerName: partner?.display_name || 'your partner',
    mePerson: me ? personOf(me.slot) : null,
    partnerPerson: partner ? personOf(partner.slot) : null,
  };
}
