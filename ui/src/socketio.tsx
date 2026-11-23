import { io } from 'socket.io-client';

// One shared connection to the same origin, namespace "/socket.io". The signed-in
// layout connects it; nothing connects on import.
export const socket = io('/socket.io', { autoConnect: false });
