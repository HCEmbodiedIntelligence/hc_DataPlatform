import { create } from 'zustand';

interface JobCenterState {
  jobIds: readonly string[];
  addJob: (jobId: string) => void;
  removeJob: (jobId: string) => void;
  clear: () => void;
}

export const useJobCenterStore = create<JobCenterState>((set) => ({
  jobIds: [],
  addJob: (jobId) =>
    set((state) => ({ jobIds: state.jobIds.includes(jobId) ? state.jobIds : [...state.jobIds, jobId] })),
  removeJob: (jobId) => set((state) => ({ jobIds: state.jobIds.filter((id) => id !== jobId) })),
  clear: () => set({ jobIds: [] }),
}));
