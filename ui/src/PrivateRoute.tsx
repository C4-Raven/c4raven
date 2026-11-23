import { Navigate, Outlet } from 'react-router';

function isSignedIn(): boolean {
  return JSON.parse(String(localStorage.getItem('loggedIn'))) === true;
}

export default function PrivateRoute() {
  return isSignedIn() ? <Outlet /> : <Navigate to="/login" />;
}
